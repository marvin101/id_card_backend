import importlib
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import UniqueConstraint

from app.api import students as students_api
from app.core.student_rolls import student_roll_conflict_query, student_roll_key
from app.models.academic_session import AcademicSession
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.schemas.student import StudentCreate, StudentUpdate


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Database:
    def __init__(self, *values):
        self.values = iter(values)
        self.commits = 0
        self.added = []

    def execute(self, _statement):
        return _Result(next(self.values))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        pass


def _student_records():
    session = AcademicSession(id=11, uuid=uuid4(), school_id=7, name="2026-27")
    school_class = SchoolClass(id=12, uuid=uuid4(), school_id=7, name="10")
    section_a = Section(id=13, uuid=uuid4(), class_id=12, name="A")
    section_b = Section(id=14, uuid=uuid4(), class_id=12, name="B")
    student = Student(
        id=15,
        uuid=uuid4(),
        school_id=7,
        session_id=session.id,
        class_id=school_class.id,
        section_id=section_a.id,
        admission_no="A-1",
        roll_no="12",
        full_name="Asha",
        is_active=True,
        updated_at=datetime.now(timezone.utc),
        academic_session=session,
        school_class=school_class,
        section=section_a,
        custom_field_values=[],
    )
    return session, school_class, section_a, section_b, student


def _patch_student_api_dependencies(monkeypatch, school):
    monkeypatch.setattr(students_api, "get_active_school", lambda *_: school)
    monkeypatch.setattr(students_api, "require_card_data_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(students_api, "effective_student_field_map", lambda *_: {})
    monkeypatch.setattr(students_api, "reject_disabled_student_fields", lambda *_: None)
    monkeypatch.setattr(students_api, "validate_required_student_fields", lambda *_: None)
    monkeypatch.setattr(students_api, "validate_student_custom_fields", lambda *_args, **_kwargs: [])


def test_student_model_roll_constraint_is_section_scoped_and_roll_remains_nullable():
    constraints = {
        constraint.name: constraint
        for constraint in Student.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    constraint = constraints["uq_student_roll_school_session_class_section"]

    assert [column.name for column in constraint.columns] == [
        "school_id",
        "session_id",
        "class_id",
        "section_id",
        "roll_no",
    ]
    assert "uq_student_roll_school_session_class" not in constraints
    assert Student.__table__.c.roll_no.nullable is True


def test_roll_scope_key_distinguishes_section_class_and_session():
    baseline = student_roll_key(1, 10, 100, "12")

    assert baseline == student_roll_key(1, 10, 100, "12")
    assert baseline != student_roll_key(1, 10, 101, "12")
    assert baseline != student_roll_key(1, 11, 100, "12")
    assert baseline != student_roll_key(2, 10, 100, "12")


def test_roll_conflict_query_uses_complete_resulting_scope_and_exclusion():
    query = student_roll_conflict_query(
        school_id=7,
        session_id=8,
        class_id=9,
        section_id=10,
        roll_no="12",
        exclude_student_id=11,
    )
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "students.school_id = 7" in sql
    assert "students.session_id = 8" in sql
    assert "students.class_id = 9" in sql
    assert "students.section_id = 10" in sql
    assert "students.roll_no = '12'" in sql
    assert "students.id != 11" in sql


def test_individual_create_checks_the_selected_section(monkeypatch):
    session, school_class, section, _, _ = _student_records()
    school = SimpleNamespace(id=7, uuid=uuid4())
    duplicate = SimpleNamespace(id=99)
    db = _Database(session, school_class, section, None, duplicate)
    _patch_student_api_dependencies(monkeypatch, school)
    captured = {}
    real_query = students_api.student_roll_conflict_query

    def capture_query(**kwargs):
        captured.update(kwargs)
        return real_query(**kwargs)

    monkeypatch.setattr(students_api, "student_roll_conflict_query", capture_query)
    payload = StudentCreate(
        session_uuid=session.uuid,
        class_uuid=school_class.uuid,
        section_uuid=section.uuid,
        admission_no="A-2",
        roll_no="12",
        full_name="Arun",
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(students_api.create_student(
            school.uuid,
            payload.model_dump_json(),
            db=db,
            current_user=SimpleNamespace(id=1),
        ))

    assert raised.value.status_code == 409
    assert "this section" in raised.value.detail
    assert captured["section_id"] == section.id


@pytest.mark.parametrize(("duplicate", "expected_status"), [(object(), 409), (None, None)])
def test_individual_update_uses_complete_destination_state(
    monkeypatch, duplicate, expected_status
):
    session, school_class, _, section_b, student = _student_records()
    school = SimpleNamespace(id=7, uuid=uuid4())
    db = _Database(student, section_b, duplicate)
    _patch_student_api_dependencies(monkeypatch, school)
    captured = {}
    real_query = students_api.student_roll_conflict_query

    def capture_query(**kwargs):
        captured.update(kwargs)
        return real_query(**kwargs)

    monkeypatch.setattr(students_api, "student_roll_conflict_query", capture_query)
    payload = StudentUpdate(section_uuid=section_b.uuid)

    if expected_status is not None:
        with pytest.raises(HTTPException) as raised:
            students_api.update_student(
                school.uuid,
                student.uuid,
                payload,
                db=db,
                current_user=SimpleNamespace(id=1),
            )
        assert raised.value.status_code == expected_status
        assert "this section" in raised.value.detail
        assert db.commits == 0
    else:
        result = students_api.update_student(
            school.uuid,
            student.uuid,
            payload,
            db=db,
            current_user=SimpleNamespace(id=1),
        )
        assert result is student
        assert student.section_id == section_b.id
        assert db.commits == 1

    assert captured == {
        "school_id": school.id,
        "session_id": session.id,
        "class_id": school_class.id,
        "section_id": section_b.id,
        "roll_no": "12",
        "exclude_student_id": student.id,
    }


def test_roll_scope_migration_upgrade_and_downgrade(monkeypatch):
    migration = importlib.import_module(
        "migrations.versions.f2a4c6e8b0d1_scope_student_roll_to_section"
    )
    calls = []
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: calls.append(("drop", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_unique_constraint",
        lambda *args, **kwargs: calls.append(("create", args, kwargs)),
    )

    migration.upgrade()
    assert migration.down_revision == "c9e2f6a1b4d8"
    assert calls == [
        ("drop", ("uq_student_roll_school_session_class", "students"), {"type_": "unique"}),
        (
            "create",
            (
                "uq_student_roll_school_session_class_section",
                "students",
                ["school_id", "session_id", "class_id", "section_id", "roll_no"],
            ),
            {},
        ),
    ]

    calls.clear()
    migration.downgrade()
    assert calls == [
        ("drop", ("uq_student_roll_school_session_class_section", "students"), {"type_": "unique"}),
        (
            "create",
            (
                "uq_student_roll_school_session_class",
                "students",
                ["school_id", "session_id", "class_id", "roll_no"],
            ),
            {},
        ),
    ]
