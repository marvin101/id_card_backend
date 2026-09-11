from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import personnel as personnel_api
from app.core.personnel_audit import (
    record_personnel_audit,
    record_personnel_field_changes,
)
from app.models.personnel import Personnel
from app.schemas.personnel import PersonnelUpdate, PersonnelVerificationUpdate


class _Db:
    def __init__(self, values=()):
        self.values = list(values)
        self.added = []
        self.commits = 0
        self.refreshes = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        self.refreshes += 1

    def execute(self, _statement):
        values = self.values

        class Result:
            def scalars(self):
                return self

            def all(self):
                return values

        return Result()


def _personnel(*, verification_status="pending", print_count=0):
    return SimpleNamespace(
        id=17,
        uuid=uuid4(),
        school_id=3,
        personnel_type="teacher",
        employee_no="EMP-1",
        full_name="Asha Singh",
        designation="Teacher",
        department="Science",
        dob=None,
        gender=None,
        blood_group=None,
        mobile=None,
        email=None,
        address=None,
        photo_path=None,
        linked_user_uuid=None,
        verification_status=verification_status,
        correction_note=None,
        verified_at=None,
        verified_by_user_id=None,
        verified_by_user_uuid=None,
        verified_by_name=None,
        printed_at=None,
        printed_by_user_id=None,
        printed_by_user_uuid=None,
        printed_by_name=None,
        print_count=print_count,
        lifecycle_status=verification_status,
        is_active=True,
        custom_fields=[],
    )


def _authorize(monkeypatch, personnel):
    monkeypatch.setattr(
        personnel_api, "get_active_school", lambda *_: SimpleNamespace(id=3)
    )
    monkeypatch.setattr(personnel_api, "_active_personnel", lambda *_: personnel)
    monkeypatch.setattr(
        personnel_api, "require_school_admin", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        personnel_api, "require_identity_data_access", lambda *_args, **_kwargs: None
    )


def test_personnel_schema_rejects_unknown_type_and_required_field_nulls():
    with pytest.raises(ValidationError):
        PersonnelUpdate(personnel_type="contractor")
    with pytest.raises(ValidationError):
        PersonnelUpdate(employee_no=None)
    with pytest.raises(ValidationError):
        PersonnelUpdate(full_name=None)


def test_personnel_model_derives_ready_and_printed_lifecycle():
    personnel = Personnel(verification_status="verified", print_count=0)
    assert personnel.lifecycle_status == "ready_for_print"
    personnel.print_count = 2
    assert personnel.lifecycle_status == "printed"
    personnel.verification_status = "needs_correction"
    assert personnel.lifecycle_status == "needs_correction"


def test_personnel_verification_requires_note_and_audits(monkeypatch):
    personnel = _personnel()
    _authorize(monkeypatch, personnel)
    with pytest.raises(HTTPException) as error:
        personnel_api.update_personnel_verification(
            uuid4(),
            personnel.uuid,
            PersonnelVerificationUpdate(status="needs_correction"),
            db=_Db(),
            current_user=SimpleNamespace(id=11),
        )
    assert error.value.status_code == 422

    db = _Db()
    result = personnel_api.update_personnel_verification(
        uuid4(),
        personnel.uuid,
        PersonnelVerificationUpdate(status="verified"),
        db=db,
        current_user=SimpleNamespace(id=11),
    )
    assert result.verification_status == "verified"
    assert result.verified_by_user_id == 11
    assert result.verified_at is not None
    assert db.commits == 1
    assert db.added[0].event_type == "verification_status_changed"


def test_personnel_mark_printed_requires_verified_and_increments(monkeypatch):
    personnel = _personnel()
    _authorize(monkeypatch, personnel)
    with pytest.raises(HTTPException) as error:
        personnel_api.mark_personnel_printed(
            uuid4(), personnel.uuid, db=_Db(), current_user=SimpleNamespace(id=22)
        )
    assert error.value.status_code == 409

    personnel.verification_status = "verified"
    db = _Db()
    personnel_api.mark_personnel_printed(
        uuid4(), personnel.uuid, db=db, current_user=SimpleNamespace(id=22)
    )
    assert personnel.print_count == 1
    assert personnel.printed_by_user_id == 22
    assert db.added[0].event_type == "marked_printed"


def test_personnel_audit_reuses_sensitive_field_protection():
    personnel = _personnel()
    db = _Db()
    record_personnel_field_changes(
        db,
        personnel=personnel,
        actor=SimpleNamespace(id=1),
        changes={"full_name": ("Asha", "Asha"), "department": ("Math", "Science")},
    )
    assert len(db.added) == 1
    assert db.added[0].field_name == "department"
    with pytest.raises(ValueError, match="Sensitive fields"):
        record_personnel_audit(
            db,
            personnel=personnel,
            actor=SimpleNamespace(id=1),
            event_type="personnel_field_updated",
            field_name="custom_fields.secret",
            new_value="do-not-store",
        )


def test_personnel_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]
    base = "/schools/{school_uuid}/personnel"
    record = f"{base}/{{personnel_uuid}}"
    assert {"get", "post"}.issubset(paths[base])
    assert {"get", "put", "delete"}.issubset(paths[record])
    assert "patch" in paths[f"{record}/verification"]
    assert "post" in paths[f"{record}/mark-printed"]
    assert "get" in paths[f"{record}/history"]
    assert "post" in paths[f"{base}/batch-verify"]
    assert "post" in paths[f"{base}/batch-mark-printed"]


def test_personnel_migration_is_single_head_with_rls_and_dynamic_field_types():
    migration = (
        Path(__file__).parents[1]
        / "migrations/versions/d7e4a10b9c82_add_personnel_workflow_foundation.py"
    ).read_text()
    assert 'down_revision: Union[str, Sequence[str], None] = "c6d2e9f4a731"' in migration
    assert "personnel_custom_field_values" in migration
    assert "personnel_audit_events" in migration
    assert "'student', 'teacher', 'staff'" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "rolbypassrls" in migration
    assert "CREATE POLICY" not in migration.upper()
