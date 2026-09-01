import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.requests import Request

from app.api.public_forms import (
    _active_form,
    _manager,
    _public_fields,
    management_router,
    public_router,
    submit_public_form,
)
from app.core.student_audit import record_student_audit
from app.core.rate_limit import enforce_public_form_rate_limit, public_form_rate_limiter
from app.core.config import settings
from app.core.file_storage import MAX_STUDENT_PHOTO_SIZE
from app.schemas.public_form import PublicFormConfigWrite, PublicStudentInput
from app.models.student import Student
from app.models.student_audit_event import StudentAuditEvent


REQUIRED = ["session_uuid", "class_uuid", "section_uuid", "admission_no", "full_name"]


class _Scalars:
    def __init__(self, values): self.values = values
    def all(self): return self.values


class _Result:
    def __init__(self, values): self.values = values
    def scalar_one_or_none(self): return self.values[0] if self.values else None
    def scalars(self): return _Scalars(self.values)


class _Database:
    def __init__(self, *results): self.results = iter(results); self.added = []
    def execute(self, _statement): return _Result(next(self.results))
    def add(self, value): self.added.append(value)


def _user(role=None, *, platform=False):
    return SimpleNamespace(id=5, platform_role="platform_admin" if platform else None, is_platform_admin=False, role=role)


def _school():
    return SimpleNamespace(id=10, uuid=uuid4(), is_active=True)


def test_public_form_config_requires_student_creation_fields():
    with pytest.raises(ValidationError, match="Required system field"):
        PublicFormConfigWrite(title="Form", selected_system_fields=["full_name"])
    assert PublicFormConfigWrite(title="Form", selected_system_fields=REQUIRED).selected_system_fields == REQUIRED
    with pytest.raises(ValidationError, match="title cannot be blank"):
        PublicFormConfigWrite(title="   ", selected_system_fields=REQUIRED)


@pytest.mark.parametrize("field", ["verification_status", "printed_at", "print_count", "school_id", "photo_path"])
def test_public_payload_rejects_lifecycle_and_internal_fields(field):
    with pytest.raises(ValidationError):
        PublicStudentInput.model_validate({field: "forbidden"})


def test_platform_admin_can_manage_any_school():
    school = _school()
    assert _manager(_Database([school]), school.uuid, _user(platform=True)) is school


@pytest.mark.parametrize("role", ["school_admin", "admin"])
def test_assigned_school_admin_can_manage_form(role):
    school = _school()
    access = SimpleNamespace(user_id=5, school_id=school.id, role=role)
    assert _manager(_Database([school], [access]), school.uuid, _user(role)) is school


@pytest.mark.parametrize("role", ["card_operator", "teacher", "staff"])
def test_non_admin_roles_cannot_manage_form(role):
    school = _school()
    access = SimpleNamespace(user_id=5, school_id=school.id, role=role)
    with pytest.raises(HTTPException) as raised:
        _manager(_Database([school], [access]), school.uuid, _user(role))
    assert raised.value.status_code == 403


def test_unassigned_school_admin_cannot_manage_another_school():
    school = _school()
    with pytest.raises(HTTPException) as raised:
        _manager(_Database([school], []), school.uuid, _user("school_admin"))
    assert raised.value.status_code == 403


@pytest.mark.parametrize("form", [None, SimpleNamespace(is_active=False, expires_at=None)])
def test_invalid_and_inactive_public_links_fail_identically(form):
    with pytest.raises(HTTPException) as raised:
        _active_form(_Database([] if form is None else [form]), "opaque-token")
    assert raised.value.status_code == 404
    assert raised.value.detail == "Public form not found"


def test_expired_public_link_returns_safe_not_found():
    form = SimpleNamespace(is_active=True, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(HTTPException) as raised:
        _active_form(_Database([form]), "opaque-token")
    assert raised.value.status_code == 404


def test_public_field_metadata_omits_inactive_custom_fields_and_database_ids():
    session = SimpleNamespace(id=1, uuid=uuid4(), name="2026")
    school_class = SimpleNamespace(id=2, uuid=uuid4(), name="Grade 5")
    section = SimpleNamespace(id=3, uuid=uuid4(), class_id=2, name="A")
    active = SimpleNamespace(uuid=uuid4(), field_key="house", label="House", data_type="text", is_required=False)
    form = SimpleNamespace(
        school_id=10, selected_system_fields=REQUIRED, selected_custom_field_uuids=[str(active.uuid), str(uuid4())], require_all_fields=False
    )
    fields = _public_fields(_Database([session], [school_class], [section], [active]), form)
    dumped = [field.model_dump(mode="json") for field in fields]
    assert len([item for item in dumped if item["kind"] == "custom"]) == 1
    assert all("id" not in item and "school_id" not in item for item in dumped)
    assert all("parent_uuid" in item for item in dumped[2]["options"])


def test_public_routes_are_registered_without_auth_dependency_on_route():
    routes = [*public_router.routes, *management_router.routes]
    route_map = {(route.path, method) for route in routes for method in getattr(route, "methods", set())}
    assert ("/public/forms/{token}", "GET") in route_map
    assert ("/public/forms/{token}/submissions", "POST") in route_map
    assert ("/schools/{school_uuid}/public-form", "PUT") in route_map
    assert ("/schools/{school_uuid}/public-form/regenerate-link", "POST") in route_map
    for route in public_router.routes:
        assert all(dependency.call.__name__ != "get_current_user" for dependency in route.dependant.dependencies)


def test_public_audit_event_supports_anonymous_actor_without_token_metadata():
    student = SimpleNamespace(id=20, school_id=10)
    db = _Database()
    event = record_student_audit(
        db, student=student, actor=None, event_type="student_created",
        new_value={"source": "public_form"}, note="Submitted through Public Form",
    )
    assert event.actor_user_id is None
    assert event.new_value == {"source": "public_form"}
    assert "token" not in str(event.new_value).lower()


def test_public_form_migration_has_single_parent_rls_and_no_client_policy():
    migration = (Path(__file__).parents[1] / "migrations" / "versions" / "a7c91e42d6b3_add_public_forms.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "f3a6c2d9e814"' in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "CREATE POLICY" not in migration.upper()
    assert "uq_public_form_school" in migration and "uq_public_form_token" in migration


class _SubmissionDatabase(_Database):
    def __init__(self, *results, fail_commit=False):
        super().__init__(*results)
        self.fail_commit = fail_commit
        self.committed = False
        self.rolled_back = False

    def flush(self):
        student = next(item for item in self.added if isinstance(item, Student))
        student.id = 99
        student.uuid = uuid4()

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")
        self.committed = True

    def rollback(self): self.rolled_back = True


def _request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})


def _submission_form(*, allow_photo=False):
    return SimpleNamespace(
        id=1, school_id=10, is_active=True, expires_at=None,
        selected_system_fields=REQUIRED, selected_custom_field_uuids=[],
        require_all_fields=False, allow_photo=allow_photo, success_message=None,
    )


def _submission_json():
    return json.dumps({
        "session_uuid": str(uuid4()), "class_uuid": str(uuid4()), "section_uuid": str(uuid4()),
        "admission_no": "A-100", "full_name": "Public Student", "custom_fields": [],
    })


def _academic_records(payload_json):
    payload = json.loads(payload_json)
    return (
        SimpleNamespace(id=11, uuid=UUID(payload["session_uuid"])),
        SimpleNamespace(id=12, uuid=UUID(payload["class_uuid"])),
        SimpleNamespace(id=13, uuid=UUID(payload["section_uuid"]), class_id=12),
    )


def test_public_submission_creates_only_pending_student_and_origin_audit(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form()], [session], [school_class], [section], [])
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    response = asyncio.run(submit_public_form("token", _request(), payload_json, None, db))
    student = next(item for item in db.added if isinstance(item, Student))
    audit = next(item for item in db.added if isinstance(item, StudentAuditEvent))
    assert response.submitted is True and db.committed is True
    assert student.verification_status == "pending"
    assert student.verified_at is None and student.printed_at is None and student.print_count == 0
    assert audit.new_value["source"] == "public_form"


def test_public_submission_rejects_unconfigured_system_field(monkeypatch):
    form = _submission_form()
    db = _SubmissionDatabase([form])
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    payload = json.loads(_submission_json())
    payload["mobile"] = "12345"
    with pytest.raises(HTTPException, match="not enabled"):
        asyncio.run(submit_public_form("token", _request(), json.dumps(payload), None, db))


def test_public_submission_duplicate_admission_is_conflict(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form()], [session], [school_class], [section], [SimpleNamespace(id=77)])
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(submit_public_form("token", _request(), payload_json, None, db))
    assert raised.value.status_code == 409


def test_public_submission_rejects_photo_when_disabled(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form()], [session], [school_class], [section], [])
    photo = UploadFile(filename="photo.png", file=io.BytesIO(b"image"), headers=Headers({"content-type": "image/png"}))
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException, match="not enabled"):
        asyncio.run(submit_public_form("token", _request(), payload_json, photo, db))


def test_public_photo_success_uses_managed_student_path(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form(allow_photo=True)], [session], [school_class], [section], [])
    photo = UploadFile(filename="photo.png", file=io.BytesIO(b"image"), headers=Headers({"content-type": "image/png"}))
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.api.public_forms.save_student_photo", lambda student_uuid, content, content_type: f"students/{student_uuid}/photo_test.png")
    asyncio.run(submit_public_form("token", _request(), payload_json, photo, db))
    student = next(item for item in db.added if isinstance(item, Student))
    assert student.photo_path == f"students/{student.uuid}/photo_test.png"


def test_storage_failure_rolls_back_without_committing_student(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form(allow_photo=True)], [session], [school_class], [section], [])
    photo = UploadFile(filename="photo.png", file=io.BytesIO(b"image"), headers=Headers({"content-type": "image/png"}))
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.api.public_forms.save_student_photo", lambda *args: (_ for _ in ()).throw(ValueError("storage failed")))
    with pytest.raises(HTTPException, match="storage failed"):
        asyncio.run(submit_public_form("token", _request(), payload_json, photo, db))
    assert db.rolled_back is True and db.committed is False


def test_database_failure_after_photo_upload_cleans_object(monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form(allow_photo=True)], [session], [school_class], [section], [], fail_commit=True)
    photo = UploadFile(filename="photo.png", file=io.BytesIO(b"image"), headers=Headers({"content-type": "image/png"}))
    deleted = []
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.api.public_forms.save_student_photo", lambda student_uuid, content, content_type: f"students/{student_uuid}/photo_test.png")
    monkeypatch.setattr("app.api.public_forms.delete_storage_object", deleted.append)
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(submit_public_form("token", _request(), payload_json, photo, db))
    assert db.rolled_back is True
    assert deleted and deleted[0].endswith("/photo_test.png")


def test_public_post_rate_limit_uses_its_own_bucket(monkeypatch):
    public_form_rate_limiter.reset()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "public_form_submit_rate_limit_requests", 1)
    enforce_public_form_rate_limit(_request(), submission=True)
    with pytest.raises(HTTPException) as raised:
        enforce_public_form_rate_limit(_request(), submission=True)
    assert raised.value.status_code == 429
    public_form_rate_limiter.reset()


@pytest.mark.parametrize(
    ("content_type", "oversized", "message"),
    [
        ("image/gif", False, "Only JPEG, PNG and WebP"),
        ("image/png", True, "must not exceed"),
    ],
)
def test_public_photo_type_and_size_validation(content_type, oversized, message, monkeypatch):
    payload_json = _submission_json()
    session, school_class, section = _academic_records(payload_json)
    db = _SubmissionDatabase([_submission_form(allow_photo=True)], [session], [school_class], [section], [])
    content = b"x" * (MAX_STUDENT_PHOTO_SIZE + 1) if oversized else b"not-a-supported-image"
    photo = UploadFile(filename="photo.bin", file=io.BytesIO(content), headers=Headers({"content-type": content_type}))
    monkeypatch.setattr("app.api.public_forms.enforce_public_form_rate_limit", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException, match=message):
        asyncio.run(submit_public_form("token", _request(), payload_json, photo, db))
    assert db.rolled_back is True and db.committed is False
