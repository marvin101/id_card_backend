from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.custom_fields import validate_student_custom_fields
from app.core.custom_fields import replace_student_custom_fields
from app.models.custom_field import CustomFieldDefinition
from app.models.student import Student
from app.schemas.student import (
    StudentCustomFieldInput,
    StudentResponse,
)
from app.api.student_fields import _require_manager


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class _Database:
    def __init__(self, *results):
        self._results = iter(results)
        self.deleted = []

    def execute(self, _statement):
        return _Result(next(self._results))

    def delete(self, value):
        self.deleted.append(value)


def _definition(*, data_type="text", required=False, active=True, school_id=10):
    return SimpleNamespace(
        id=1,
        uuid=uuid4(),
        school_id=school_id,
        entity_type="student",
        field_key="test_field",
        label="Test Field",
        data_type=data_type,
        is_required=required,
        is_active=active,
    )


def _validate(definition, value, *, required_definitions=None):
    db = _Database([definition], required_definitions or [])
    return validate_student_custom_fields(
        db,
        10,
        [StudentCustomFieldInput(field_uuid=definition.uuid, value=value)],
        require_all=True,
    )


def test_number_and_date_values_are_validated_and_normalized():
    number = _definition(data_type="number")
    assert _validate(number, " 12.50 ")[0][1] == "12.5"
    date = _definition(data_type="date")
    assert _validate(date, "2026-08-30")[0][1] == "2026-08-30"


@pytest.mark.parametrize(
    ("data_type", "value", "message"),
    [("number", "twelve", "must be a number"), ("date", "30/08/2026", "YYYY-MM-DD")],
)
def test_malformed_typed_values_are_rejected(data_type, value, message):
    with pytest.raises(HTTPException, match=message):
        _validate(_definition(data_type=data_type), value)


def test_phone_validation_is_conservative_and_international_friendly():
    phone = _definition(data_type="phone")
    assert _validate(phone, "+44 20 7946 0958")[0][1] == "+44 20 7946 0958"
    with pytest.raises(HTTPException, match="valid phone"):
        _validate(phone, "12x")


def test_duplicate_field_uuid_is_rejected_before_lookup():
    definition = _definition()
    submitted = [
        StudentCustomFieldInput(field_uuid=definition.uuid, value="one"),
        StudentCustomFieldInput(field_uuid=definition.uuid, value="two"),
    ]
    with pytest.raises(HTTPException, match="Duplicate"):
        validate_student_custom_fields(_Database(), 10, submitted, require_all=True)


def test_unknown_or_other_school_field_is_rejected():
    submitted = [StudentCustomFieldInput(field_uuid=uuid4(), value="value")]
    with pytest.raises(HTTPException, match="does not belong"):
        validate_student_custom_fields(_Database([]), 10, submitted, require_all=False)


def test_inactive_field_is_rejected_but_existing_values_remain_storable():
    definition = _definition(active=False)
    with pytest.raises(HTTPException, match="Inactive"):
        validate_student_custom_fields(
            _Database([definition]),
            10,
            [StudentCustomFieldInput(field_uuid=definition.uuid, value="old")],
            require_all=False,
        )
    assert definition.is_active is False


def test_required_fields_are_enforced_for_full_payloads():
    required = _definition(required=True)
    with pytest.raises(HTTPException, match="missing"):
        validate_student_custom_fields(
            _Database([required]), 10, [], require_all=True
        )
    with pytest.raises(HTTPException, match="required"):
        _validate(required, "", required_definitions=[required])


def test_empty_legacy_payload_remains_valid_when_school_has_no_required_fields():
    assert validate_student_custom_fields(
        _Database([], []), 10, [], require_all=True
    ) == []


def test_forward_migration_enables_rls_without_client_policy():
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "d1e4f7a8b901_add_student_custom_fields.py"
    ).read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert '"custom_field_definitions"' in migration
    assert '"student_custom_field_values"' in migration
    assert "rolbypassrls" in migration
    assert "CREATE POLICY" not in migration.upper()


@pytest.mark.parametrize("role", ["card_operator", "teacher", "staff"])
def test_non_admin_school_roles_cannot_manage_field_definitions(role):
    user = SimpleNamespace(id=1, platform_role=None, is_platform_admin=False)
    access = SimpleNamespace(user_id=1, school_id=10, role=role)
    with pytest.raises(HTTPException) as raised:
        _require_manager(_Database([access]), user, 10)
    assert raised.value.status_code == 403


@pytest.mark.parametrize("role", ["school_admin", "admin"])
def test_school_admin_roles_can_manage_field_definitions(role):
    user = SimpleNamespace(id=1, platform_role=None, is_platform_admin=False)
    access = SimpleNamespace(user_id=1, school_id=10, role=role)
    assert _require_manager(_Database([access]), user, 10) is None


def test_platform_admin_can_manage_field_definitions_without_assignment():
    user = SimpleNamespace(
        id=1, platform_role="platform_admin", is_platform_admin=False
    )
    assert _require_manager(_Database(), user, 10) is None


def test_custom_value_create_read_and_update_round_trip():
    definition = CustomFieldDefinition(
        id=1,
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
    student = Student(custom_field_values=[])
    db = _Database()

    replace_student_custom_fields(db, student, [(definition, "Blue")])
    assert student.custom_fields == [
        {
            "field_uuid": definition.uuid,
            "field_key": "house",
            "label": "House",
            "data_type": "text",
            "value": "Blue",
            "is_active": True,
        }
    ]

    replace_student_custom_fields(db, student, [(definition, "Green")])
    assert student.custom_fields[0]["value"] == "Green"

    definition.is_active = False
    assert student.custom_fields[0]["value"] == "Green"
    assert student.custom_fields[0]["is_active"] is False
    replace_student_custom_fields(db, student, [])
    assert db.deleted == []


def test_legacy_student_response_without_custom_fields_remains_valid():
    response = StudentResponse.model_validate(
        {
            "uuid": uuid4(),
            "session_uuid": uuid4(),
            "class_uuid": uuid4(),
            "section_uuid": uuid4(),
            "admission_no": "A-1",
            "roll_no": None,
            "stream": None,
            "full_name": "Legacy Student",
            "father_name": None,
            "mother_name": None,
            "dob": None,
            "gender": None,
            "blood_group": None,
            "mobile": None,
            "aadhaar": None,
            "address": None,
            "photo_path": None,
            "is_active": True,
        }
    )
    assert response.custom_fields == []
