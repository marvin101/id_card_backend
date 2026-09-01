from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import students as students_api
from app.core.student_audit import audit_value, record_student_field_changes
from app.models.student import Student
from app.schemas.student import (
    StudentBatchRequest,
    StudentVerificationUpdate,
    VerificationStatus,
)


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


def _student(*, status="pending", print_count=0):
    return SimpleNamespace(
        id=7,
        uuid=uuid4(),
        school_id=3,
        verification_status=status,
        correction_note=None,
        verified_at=None,
        verified_by_user_id=None,
        printed_at=None,
        printed_by_user_id=None,
        print_count=print_count,
        session_uuid=uuid4(),
        class_uuid=uuid4(),
        section_uuid=uuid4(),
        admission_no="A-1",
        roll_no=None,
        stream=None,
        full_name="Asha Singh",
        father_name=None,
        mother_name=None,
        dob=None,
        gender=None,
        blood_group=None,
        mobile=None,
        aadhaar=None,
        address=None,
        photo_path=None,
        is_active=True,
        lifecycle_status=status,
        verified_by_user_uuid=None,
        verified_by_name=None,
        printed_by_user_uuid=None,
        printed_by_name=None,
        custom_fields=[],
    )


def _authorize(monkeypatch, student):
    monkeypatch.setattr(students_api, "get_active_school", lambda *_: SimpleNamespace(id=3))
    monkeypatch.setattr(students_api, "_active_student", lambda *_: student)
    monkeypatch.setattr(students_api, "require_school_admin", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(students_api, "require_card_data_access", lambda *_args, **_kwargs: None)


def test_verification_status_validation_and_correction_note_requirement(monkeypatch):
    with pytest.raises(ValidationError):
        StudentVerificationUpdate(status="printed")

    student = _student()
    _authorize(monkeypatch, student)
    with pytest.raises(HTTPException) as error:
        students_api.update_student_verification(
            uuid4(), student.uuid,
            StudentVerificationUpdate(status=VerificationStatus.NEEDS_CORRECTION),
            db=_Db(), current_user=SimpleNamespace(id=11),
        )
    assert error.value.status_code == 422
    assert student.verification_status == "pending"


def test_verify_sets_actor_timestamp_and_audits(monkeypatch):
    student = _student()
    _authorize(monkeypatch, student)
    db = _Db()
    result = students_api.update_student_verification(
        uuid4(), student.uuid,
        StudentVerificationUpdate(status=VerificationStatus.VERIFIED),
        db=db, current_user=SimpleNamespace(id=11),
    )
    assert result.verification_status == "verified"
    assert result.verified_by_user_id == 11
    assert result.verified_at is not None
    assert db.commits == 1
    assert db.added[0].event_type == "verification_status_changed"


def test_needs_correction_stores_note_and_reset_pending_clears_it(monkeypatch):
    student = _student()
    _authorize(monkeypatch, student)
    students_api.update_student_verification(
        uuid4(), student.uuid,
        StudentVerificationUpdate(status="needs_correction", note="Photo is blurred"),
        db=_Db(), current_user=SimpleNamespace(id=11),
    )
    assert student.correction_note == "Photo is blurred"
    students_api.update_student_verification(
        uuid4(), student.uuid,
        StudentVerificationUpdate(status="pending"),
        db=_Db(), current_user=SimpleNamespace(id=11),
    )
    assert student.correction_note is None


def test_mark_printed_and_reprint_increment_and_audit(monkeypatch):
    student = _student(status="verified")
    _authorize(monkeypatch, student)
    actor = SimpleNamespace(id=22)
    first_db = _Db()
    students_api.mark_student_printed(uuid4(), student.uuid, db=first_db, current_user=actor)
    assert student.print_count == 1
    assert student.printed_at is not None
    assert student.printed_by_user_id == 22
    assert first_db.added[0].event_type == "marked_printed"

    second_db = _Db()
    students_api.mark_student_printed(uuid4(), student.uuid, db=second_db, current_user=actor)
    assert student.print_count == 2
    assert second_db.added[0].event_type == "reprinted"


def test_pending_student_cannot_be_marked_printed(monkeypatch):
    student = _student()
    _authorize(monkeypatch, student)
    db = _Db()
    with pytest.raises(HTTPException) as error:
        students_api.mark_student_printed(uuid4(), student.uuid, db=db, current_user=SimpleNamespace(id=2))
    assert error.value.status_code == 409
    assert student.print_count == 0
    assert db.commits == 0


def test_unauthorized_verification_does_not_mutate(monkeypatch):
    student = _student()
    monkeypatch.setattr(students_api, "get_active_school", lambda *_: SimpleNamespace(id=3))
    monkeypatch.setattr(
        students_api,
        "require_school_admin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(status_code=403)),
    )
    with pytest.raises(HTTPException):
        students_api.update_student_verification(
            uuid4(), student.uuid, StudentVerificationUpdate(status="verified"),
            db=_Db(), current_user=SimpleNamespace(id=4),
        )
    assert student.verification_status == "pending"


def test_batch_verify_and_batch_print_create_one_audit_per_student(monkeypatch):
    students = [_student(), _student()]
    monkeypatch.setattr(students_api, "get_active_school", lambda *_: SimpleNamespace(id=3))
    monkeypatch.setattr(students_api, "require_school_admin", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(students_api, "require_card_data_access", lambda *_args, **_kwargs: None)
    payload = StudentBatchRequest(student_uuids=[item.uuid for item in students])
    verify_db = _Db(students)
    result = students_api.batch_verify_students(uuid4(), payload, db=verify_db, current_user=SimpleNamespace(id=8))
    assert result.updated_count == 2
    assert all(item.verification_status == "verified" for item in students)
    assert len(verify_db.added) == 2

    print_db = _Db(students)
    result = students_api.batch_mark_students_printed(uuid4(), payload, db=print_db, current_user=SimpleNamespace(id=9))
    assert result.updated_count == 2
    assert all(item.print_count == 1 for item in students)
    assert len(print_db.added) == 2


def test_audit_helper_logs_only_changed_fields_and_json_safe_values():
    db = _Db()
    student = _student()
    record_student_field_changes(
        db, student=student, actor=SimpleNamespace(id=1),
        changes={"full_name": ("Asha", "Asha"), "dob": (None, "2010-01-01")},
    )
    assert len(db.added) == 1
    assert db.added[0].field_name == "dob"
    assert audit_value(uuid4()) is not None


def test_lifecycle_model_derives_ready_and_printed_without_persisted_drift():
    student = Student(verification_status="verified", print_count=0)
    assert student.lifecycle_status == "ready_for_print"
    student.print_count = 2
    assert student.lifecycle_status == "printed"
    student.verification_status = "needs_correction"
    assert student.lifecycle_status == "needs_correction"


def test_forward_migration_has_defaults_indexes_rls_and_no_client_policy():
    migration = (
        Path(__file__).parents[1]
        / "migrations/versions/f3a6c2d9e814_add_student_lifecycle_and_audit.py"
    ).read_text()
    assert 'server_default="pending"' in migration
    assert 'server_default="0"' in migration
    assert "student_audit_events" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "rolbypassrls" in migration
    assert "CREATE POLICY" not in migration.upper()
    assert "ix_students_school_verification" in migration
    assert 'down_revision: Union[str, Sequence[str], None] = "5683bf325b3d"' in migration


def test_lifecycle_routes_are_registered():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "patch" in paths["/schools/{school_uuid}/students/{student_uuid}/verification"]
    assert "post" in paths["/schools/{school_uuid}/students/{student_uuid}/mark-printed"]
    assert "get" in paths["/schools/{school_uuid}/students/{student_uuid}/history"]
    assert "post" in paths["/schools/{school_uuid}/students/batch-verify"]
    assert "post" in paths["/schools/{school_uuid}/students/batch-mark-printed"]


def test_history_query_is_school_student_scoped_and_newest_first(monkeypatch):
    student = _student()
    _authorize(monkeypatch, student)

    class HistoryDb(_Db):
        def execute(self, statement):
            self.statement = statement
            return super().execute(statement)

    db = HistoryDb([])
    assert students_api.get_student_history(
        uuid4(), student.uuid, db=db, current_user=SimpleNamespace(id=1)
    ) == []
    sql = str(db.statement)
    assert "student_audit_events.school_id" in sql
    assert "student_audit_events.student_id" in sql
    assert "student_audit_events.created_at DESC" in sql
    assert "student_audit_events.id DESC" in sql


def test_student_list_filters_verification_and_printed(monkeypatch):
    monkeypatch.setattr(students_api, "get_active_school", lambda *_: SimpleNamespace(id=3))
    monkeypatch.setattr(students_api, "require_card_data_access", lambda *_args, **_kwargs: None)

    class FilterDb(_Db):
        def execute(self, statement):
            self.statement = statement
            return super().execute(statement)

    db = FilterDb([])
    students_api.list_students(
        uuid4(), admission_no=None, session_uuid=None, class_uuid=None,
        section_uuid=None, verification_status=VerificationStatus.VERIFIED,
        printed=False, db=db, current_user=SimpleNamespace(id=1),
    )
    sql = str(db.statement)
    assert "students.verification_status" in sql
    assert "students.print_count" in sql
