from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.public_verification import public_router
from app.core.database import get_db
from app.core.rate_limit import public_verification_rate_limiter
from app.core.security import get_current_user
from app.main import app
from app.models.school import School
from app.models.student import Student
from app.models.user_school_access import UserSchoolAccess
from app.schemas.card_template import validate_design_document


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Database:
    def __init__(self, school, student, access):
        self.school = school
        self.student = student
        self.access = access
        self.commits = 0
        self.added = []

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = set(statement.compile().params.values())
        if entity is School:
            return _Result(self.school if self.school.uuid in params else None)
        if entity is UserSchoolAccess:
            return _Result(
                self.access
                if {self.access.user_id, self.access.school_id}.issubset(params)
                else None
            )
        if entity is Student:
            if self.student.uuid in params:
                return _Result(self.student)
            if self.student.public_verification_token in params:
                if (
                    self.student.public_verification_enabled
                    and self.student.is_active
                ):
                    return _Result(self.student)
                return _Result()
            return _Result()
        raise AssertionError(f"Unexpected query entity: {entity}")

    def commit(self):
        self.commits += 1

    def add(self, value):
        self.added.append(value)


def _fixture(*, school_enabled=True, student_enabled=True, role="school_admin"):
    school = SimpleNamespace(
        id=10,
        uuid=uuid4(),
        school_code="SCH-01",
        school_name="Campus School",
        logo_path=None,
        is_active=True,
        public_verification_enabled=school_enabled,
        public_verification_fields=["full_name", "class", "section"],
    )
    student = SimpleNamespace(
        id=20,
        uuid=uuid4(),
        school_id=school.id,
        school=school,
        full_name="Student Name",
        admission_no="ADM-001",
        roll_no="7",
        stream="Science",
        academic_session=SimpleNamespace(name="2026-27"),
        school_class=SimpleNamespace(name="10"),
        section=SimpleNamespace(name="A"),
        photo_path=None,
        verification_status="verified",
        lifecycle_status="ready_for_print",
        verified_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        is_active=True,
        public_verification_token="opaque-verification-token-1234567890",
        public_verification_enabled=student_enabled,
        verification_url="https://app.example/verify/opaque-verification-token-1234567890",
    )
    user = SimpleNamespace(id=1, is_platform_admin=False, platform_role=None)
    access = SimpleNamespace(user_id=user.id, school_id=school.id, role=role)
    return school, student, user, _Database(school, student, access)


@pytest.fixture(autouse=True)
def _clean_app_state():
    app.dependency_overrides.clear()
    public_verification_rate_limiter.reset()
    yield
    app.dependency_overrides.clear()
    public_verification_rate_limiter.reset()


def _override(db, user=None):
    def database_dependency():
        yield db

    app.dependency_overrides[get_db] = database_dependency
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user


def test_public_verification_is_anonymous_and_discloses_only_configured_fields():
    _, student, _, db = _fixture()
    _override(db)
    with TestClient(app) as client:
        response = client.get(
            f"/public/verifications/{student.public_verification_token}"
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["school"]["school_name"] == "Campus School"
    assert response.json()["fields"] == [
        {"key": "full_name", "label": "Full name", "value": "Student Name"},
        {"key": "class", "label": "Class", "value": "10"},
        {"key": "section", "label": "Section", "value": "A"},
    ]
    assert "public_verification_token" not in response.text
    assert "uuid" not in response.text
    assert "admission_no" not in response.text
    route = next(route for route in public_router.routes if "GET" in route.methods)
    assert all(
        dependency.call.__name__ != "get_current_user"
        for dependency in route.dependant.dependencies
    )


@pytest.mark.parametrize(
    ("school_enabled", "student_enabled", "student_active"),
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_disabled_or_inactive_public_verification_returns_generic_404(
    school_enabled, student_enabled, student_active
):
    _, student, _, db = _fixture(
        school_enabled=school_enabled, student_enabled=student_enabled
    )
    student.is_active = student_active
    _override(db)
    with TestClient(app) as client:
        response = client.get(
            f"/public/verifications/{student.public_verification_token}"
        )
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Verification record not found"}


def test_admin_controls_school_public_verification_disclosure():
    school, _, user, db = _fixture(school_enabled=False)
    _override(db, user)
    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/public-verification",
            json={"enabled": True, "fields": ["full_name", "photo"]},
        )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["fields"] == ["full_name", "photo"]
    assert school.public_verification_fields == ["full_name", "photo"]
    assert db.commits == 1


def test_non_admin_cannot_manage_public_verification():
    school, _, user, db = _fixture(role="card_operator")
    _override(db, user)
    with TestClient(app) as client:
        response = client.get(f"/schools/{school.uuid}/public-verification")
    assert response.status_code == 403


def test_admin_can_revoke_and_regenerate_one_student_link():
    school, student, user, db = _fixture()
    original_token = student.public_verification_token
    _override(db, user)
    with TestClient(app) as client:
        disabled = client.put(
            f"/schools/{school.uuid}/students/{student.uuid}/public-verification",
            json={"enabled": False},
        )
        regenerated = client.post(
            f"/schools/{school.uuid}/students/{student.uuid}/public-verification/regenerate-link"
        )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert regenerated.status_code == 200
    assert regenerated.json()["enabled"] is True
    assert student.public_verification_token != original_token
    assert db.commits == 2
    assert len(db.added) == 2


def test_public_verification_fields_reject_sensitive_and_duplicate_values():
    school, _, user, db = _fixture()
    _override(db, user)
    with TestClient(app) as client:
        sensitive = client.put(
            f"/schools/{school.uuid}/public-verification",
            json={"enabled": True, "fields": ["mobile"]},
        )
        duplicate = client.put(
            f"/schools/{school.uuid}/public-verification",
            json={"enabled": True, "fields": ["full_name", "full_name"]},
        )
    assert sensitive.status_code == 422
    assert duplicate.status_code == 422
    assert db.commits == 0


def test_verification_url_is_valid_only_as_a_single_qr_binding():
    base = {
        "schema_version": 2,
        "canvas": {
            "width": 54,
            "height": 86,
            "orientation": "portrait",
            "background_color": "#FFFFFF",
        },
        "settings": {},
    }
    qr = {
        **base,
        "elements": [{
            "id": "verification-qr",
            "type": "qr_code",
            "x": 5,
            "y": 5,
            "width": 20,
            "height": 20,
            "rotation": 0,
            "z_index": 0,
            "locked": False,
            "visible": True,
            "style": {},
            "data": {"field": "verification_url"},
        }],
    }
    assert validate_design_document(qr) == qr

    qr["elements"][0]["type"] = "bound_text"
    with pytest.raises(ValueError, match="unknown student field binding"):
        validate_design_document(qr)


def test_public_verification_migration_adds_revocable_tokens_and_is_head():
    root = Path(__file__).parents[1]
    migration = (
        root
        / "migrations"
        / "versions"
        / "a1d4e7f9b2c5_add_public_student_verification.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "e4c7a91d2f60"' in migration
    assert "public_verification_token" in migration
    assert "public_verification_enabled" in migration
    assert "unique=True" in migration
