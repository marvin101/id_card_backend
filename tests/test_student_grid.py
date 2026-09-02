from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import student_grid
from app.models.academic_session import AcademicSession
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.custom_field import CustomFieldDefinition, StudentCustomFieldValue
from app.schemas.student_grid import StudentGridPatchRequest, StudentGridRowPatch


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, values=None, scalar=None):
        self.values = values or []
        self.scalar = scalar

    def scalars(self):
        return _Scalars(self.values)

    def scalar_one(self):
        return self.scalar


class _Db:
    def __init__(self, *results):
        self.results = iter(results)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, _statement):
        return next(self.results)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _academic():
    session = AcademicSession(id=11, uuid=uuid4(), school_id=10, name="2026-27")
    school_class = SchoolClass(id=21, uuid=uuid4(), school_id=10, name="10")
    section = Section(
        id=31, uuid=uuid4(), name="A", class_id=school_class.id
    )
    return session, school_class, section


def _student(session, school_class, section, *, admission="A-1", roll="1"):
    return Student(
        id=41,
        uuid=uuid4(),
        school_id=10,
        session_id=session.id,
        class_id=school_class.id,
        section_id=section.id,
        admission_no=admission,
        roll_no=roll,
        full_name="Student One",
        is_active=True,
        updated_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
        academic_session=session,
        school_class=school_class,
        section=section,
        custom_field_values=[],
    )


@pytest.fixture
def access(monkeypatch):
    school = SimpleNamespace(id=10, uuid=uuid4())
    calls = []
    monkeypatch.setattr(student_grid, "get_active_school", lambda db, uuid: school)
    monkeypatch.setattr(
        student_grid,
        "require_card_data_access",
        lambda db, user, school_id, detail: calls.append((user.role, school_id)),
    )
    return school, calls


@pytest.mark.parametrize("role", ["platform_admin", "school_admin", "admin", "card_operator"])
def test_authorized_roles_can_load_bounded_grid(access, role):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    definition = CustomFieldDefinition(
        id=50,
        uuid=uuid4(),
        school_id=10,
        entity_type="student",
        field_key="house",
        label="House",
        data_type="text",
        is_required=False,
        display_order=0,
        is_active=True,
    )
    db = _Db(
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([definition]),
        _Result(scalar=1),
        _Result([student]),
    )
    response = student_grid.get_student_grid(
        access[0].uuid,
        limit=50,
        offset=0,
        search=None,
        session_uuid=None,
        class_uuid=None,
        section_uuid=None,
        db=db,
        current_user=SimpleNamespace(role=role),
    )
    assert response.total == 1
    assert response.limit == 50
    assert response.rows[0].admission_no == "A-1"
    assert response.sections[0].class_uuid == school_class.uuid
    assert response.custom_fields[0].field_key == "house"
    assert access[1] == [(role, 10)]


def test_unauthorized_role_is_denied_before_grid_queries(monkeypatch):
    school = SimpleNamespace(id=10, uuid=uuid4())
    monkeypatch.setattr(student_grid, "get_active_school", lambda db, uuid: school)

    def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(student_grid, "require_card_data_access", deny)
    with pytest.raises(HTTPException) as raised:
        student_grid.get_student_grid(
            school.uuid,
            limit=100,
            offset=0,
            search=None,
            session_uuid=None,
            class_uuid=None,
            section_uuid=None,
            db=_Db(),
            current_user=SimpleNamespace(role="teacher"),
        )
    assert raised.value.status_code == 403


def _patch_db(student, session, school_class, section, *, definitions=None, all_students=None, refreshed=True):
    results = [
        _Result([student]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result(definitions or []),
        _Result([]),  # required custom field lookup
        _Result(all_students or [student]),
    ]
    if refreshed:
        results.append(_Result([student]))
    return _Db(*results)


def _patch(student, **system_fields):
    return StudentGridPatchRequest(
        rows=[
            StudentGridRowPatch(
                student_uuid=student.uuid,
                expected_updated_at=student.updated_at,
                system_fields=system_fields,
            )
        ]
    )


def _call(access, db, payload):
    return student_grid.patch_student_grid(
        access[0].uuid,
        payload,
        db=db,
        current_user=SimpleNamespace(id=99, role="school_admin"),
    )


def test_valid_update_commits_and_records_field_audit(access):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    db = _patch_db(student, session, school_class, section)
    result = _call(access, db, _patch(student, full_name="Updated Student"))
    assert result.updated_count == 1
    assert student.full_name == "Updated Student"
    assert db.commits == 1
    assert [(event.field_name, event.old_value, event.new_value) for event in db.added] == [
        ("full_name", "Student One", "Updated Student")
    ]


def test_valid_multi_row_update_is_one_transaction(access):
    session, school_class, section = _academic()
    first = _student(session, school_class, section, admission="A-1", roll="1")
    second = _student(session, school_class, section, admission="A-2", roll="2")
    second.id = 42
    second.uuid = uuid4()
    db = _Db(
        _Result([first, second]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([]),
        _Result([]),
        _Result([]),
        _Result([first, second]),
        _Result([first, second]),
    )
    payload = StudentGridPatchRequest(
        rows=[
            StudentGridRowPatch(
                student_uuid=first.uuid,
                expected_updated_at=first.updated_at,
                system_fields={"full_name": "First Updated"},
            ),
            StudentGridRowPatch(
                student_uuid=second.uuid,
                expected_updated_at=second.updated_at,
                system_fields={"full_name": "Second Updated"},
            ),
        ]
    )
    result = _call(access, db, payload)
    assert result.updated_count == 2
    assert db.commits == 1
    assert {event.new_value for event in db.added} == {"First Updated", "Second Updated"}


def test_invalid_second_row_prevents_first_row_mutation(access):
    session, school_class, section = _academic()
    first = _student(session, school_class, section, admission="A-1", roll="1")
    second = _student(session, school_class, section, admission="A-2", roll="2")
    second.id = 42
    second.uuid = uuid4()
    db = _Db(
        _Result([first, second]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([]),
        _Result([]),
        _Result([]),
    )
    payload = StudentGridPatchRequest(
        rows=[
            StudentGridRowPatch(student_uuid=first.uuid, system_fields={"full_name": "Would Change"}),
            StudentGridRowPatch(student_uuid=second.uuid, system_fields={"blood_group": "invalid"}),
        ]
    )
    response = _call(access, db, payload)
    assert response.status_code == 422
    assert first.full_name == "Student One"
    assert db.commits == 0
    assert db.added == []


def test_unchanged_cells_do_not_create_audit_noise(access):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    db = _patch_db(student, session, school_class, section, refreshed=False)
    result = _call(access, db, _patch(student, full_name="Student One"))
    assert result.updated_count == 0
    assert db.added == []
    assert db.commits == 1


@pytest.mark.parametrize("field", ["photo_path", "verification_status", "print_count", "school_id", "created_at"])
def test_internal_and_lifecycle_fields_are_rejected_without_commit(access, field):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    db = _patch_db(student, session, school_class, section, refreshed=False)
    response = _call(access, db, _patch(student, **{field: "forbidden"}))
    assert response.status_code == 422
    assert b"not editable" in response.body
    assert db.commits == 0


def test_stale_version_returns_row_specific_conflict(access):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    payload = _patch(student, full_name="Updated")
    payload.rows[0].expected_updated_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db = _Db(
        _Result([student]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([]),
    )
    response = _call(access, db, payload)
    assert response.status_code == 409
    assert str(student.uuid).encode() in response.body
    assert b"refresh before saving" in response.body
    assert db.commits == 0


def test_cross_school_student_is_reported_and_nothing_is_saved(access):
    student_uuid = uuid4()
    db = _Db(_Result([]))
    response = _call(
        access,
        db,
        StudentGridPatchRequest(
            rows=[StudentGridRowPatch(student_uuid=student_uuid, system_fields={"full_name": "X"})]
        ),
    )
    assert response.status_code == 422
    assert str(student_uuid).encode() in response.body
    assert b"not found in this school" in response.body
    assert db.commits == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("blood_group", "purple", b"valid blood group"),
        ("dob", "02/09/2026", b"YYYY-MM-DD"),
        ("full_name", "", b"required"),
    ],
)
def test_typed_system_validation_is_structured_and_atomic(access, field, value, message):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    db = _patch_db(student, session, school_class, section, refreshed=False)
    response = _call(access, db, _patch(student, **{field: value}))
    assert response.status_code == 422
    assert message in response.body
    assert field.encode() in response.body
    assert db.commits == 0


def test_wrong_class_section_combination_is_rejected(access):
    session, school_class, section = _academic()
    other_class = SchoolClass(id=22, uuid=uuid4(), school_id=10, name="11")
    student = _student(session, school_class, section)
    db = _Db(
        _Result([student]),
        _Result([session]),
        _Result([school_class, other_class]),
        _Result([section]),
        _Result([]),
        _Result([]),
    )
    response = _call(access, db, _patch(student, class_uuid=str(other_class.uuid)))
    assert response.status_code == 422
    assert b"selected class" in response.body
    assert db.commits == 0


def test_admission_and_roll_duplicates_are_prevalidated(access):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    existing = _student(session, school_class, section, admission="A-2", roll="2")
    existing.id = 42
    existing.uuid = uuid4()
    db = _patch_db(
        student,
        session,
        school_class,
        section,
        all_students=[student, existing],
        refreshed=False,
    )
    response = _call(access, db, _patch(student, admission_no="A-2", roll_no="2"))
    assert response.status_code == 422
    assert b"Admission number already exists" in response.body
    assert b"Roll number already exists" in response.body
    assert db.commits == 0


def test_custom_number_validation_and_inactive_field_rejection_are_structured(access):
    session, school_class, section = _academic()
    student = _student(session, school_class, section)
    definition = SimpleNamespace(
        id=50,
        uuid=uuid4(),
        school_id=10,
        entity_type="student",
        field_key="height",
        label="Height",
        data_type="number",
        is_required=False,
        is_active=True,
    )
    db = _Db(
        _Result([student]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([definition]),
        _Result([definition]),
        _Result([]),
    )
    payload = StudentGridPatchRequest(
        rows=[
            StudentGridRowPatch(
                student_uuid=student.uuid,
                expected_updated_at=student.updated_at,
                custom_fields={str(definition.uuid): "tall"},
            )
        ]
    )
    response = _call(access, db, payload)
    assert response.status_code == 422
    assert b"must be a number" in response.body
    assert db.commits == 0

    unknown = uuid4()
    db = _patch_db(student, session, school_class, section, refreshed=False)
    payload.rows[0].custom_fields = {str(unknown): "value"}
    response = _call(access, db, payload)
    assert response.status_code == 422
    assert b"inactive" in response.body
    assert db.commits == 0


def test_changed_custom_field_uses_typed_normalization_and_audit(access):
    session, school_class, section = _academic()
    definition = CustomFieldDefinition(
        id=50,
        uuid=uuid4(),
        school_id=10,
        entity_type="student",
        field_key="height",
        label="Height",
        data_type="number",
        is_required=False,
        is_active=True,
        display_order=0,
    )
    student = _student(session, school_class, section)
    student.custom_field_values = [
        StudentCustomFieldValue(
            id=60, student_id=student.id, field_definition=definition, value="150"
        )
    ]
    db = _Db(
        _Result([student]),
        _Result([session]),
        _Result([school_class]),
        _Result([section]),
        _Result([definition]),
        _Result([definition]),
        _Result([]),
        _Result([student]),
        _Result([student]),
    )
    payload = StudentGridPatchRequest(
        rows=[
            StudentGridRowPatch(
                student_uuid=student.uuid,
                expected_updated_at=student.updated_at,
                custom_fields={str(definition.uuid): "151.50"},
            )
        ]
    )
    result = _call(access, db, payload)
    assert result.updated_count == 1
    assert student.custom_field_values[0].value == "151.5"
    assert [(event.field_name, event.old_value, event.new_value) for event in db.added] == [
        ("custom_fields.height", "150", "151.5")
    ]
