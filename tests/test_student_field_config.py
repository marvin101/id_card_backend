from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.student_field_config import (
    BUILTIN_STUDENT_FIELDS,
    PROTECTED_STUDENT_FIELD_KEYS,
    effective_student_field_map,
    reject_disabled_student_fields,
    validate_required_student_fields,
)
from app.schemas.student_field_config import (
    BuiltinStudentFieldConfigWrite,
    BuiltinStudentFieldWrite,
)


def _payload(**overrides):
    fields = []
    for definition in BUILTIN_STUDENT_FIELDS.values():
        values = {
            "key": definition.key,
            "enabled": definition.default_enabled,
            "required": definition.default_required,
            "display_order": definition.default_display_order,
        }
        values.update(overrides.get(definition.key, {}))
        fields.append(BuiltinStudentFieldWrite(**values))
    return fields


def _session(rows):
    db = MagicMock(spec=Session)
    db.execute.return_value.scalars.return_value.all.return_value = rows
    return db


def test_effective_defaults_require_only_protected_fields():
    config = effective_student_field_map(_session([]), 10)
    assert set(config) == set(BUILTIN_STUDENT_FIELDS)
    assert {key for key, field in config.items() if field.required} == PROTECTED_STUDENT_FIELD_KEYS
    assert all(field.enabled for field in config.values())


def test_persisted_overrides_are_school_query_scoped_and_ordered():
    db = _session([
        SimpleNamespace(field_key="aadhaar", is_enabled=False, is_required=False, display_order=30),
        SimpleNamespace(field_key="blood_group", is_enabled=True, is_required=True, display_order=0),
    ])
    config = effective_student_field_map(db, 42)
    assert config["aadhaar"].enabled is False
    assert config["blood_group"].required is True
    assert "school_student_field_configs.school_id" in str(db.execute.call_args.args[0])


@pytest.mark.parametrize(
    "override, message",
    [
        ({"full_name": {"enabled": False}}, "Required by CampusID"),
        ({"full_name": {"required": False}}, "Required by CampusID"),
        ({"aadhaar": {"enabled": False, "required": True}}, "must be enabled"),
    ],
)
def test_write_schema_enforces_invariants(override, message):
    with pytest.raises(ValidationError, match=message):
        BuiltinStudentFieldConfigWrite(fields=_payload(**override))


def test_write_schema_rejects_unknown_duplicate_and_duplicate_order():
    fields = _payload()
    with pytest.raises(ValidationError, match="duplicated"):
        BuiltinStudentFieldConfigWrite(fields=[*fields[:-1], fields[0]])
    with pytest.raises(ValidationError, match="Display order"):
        BuiltinStudentFieldConfigWrite(
            fields=[field.model_copy(update={"display_order": 0}) if field.key == "roll_no" else field for field in fields]
        )
    with pytest.raises(ValidationError, match="Unsupported"):
        BuiltinStudentFieldConfigWrite(
            fields=[*fields[:-1], BuiltinStudentFieldWrite(key="unknown", enabled=True, required=False, display_order=99)]
        )


def test_disabled_mutation_and_required_value_validation():
    config = effective_student_field_map(
        _session([
            SimpleNamespace(field_key="aadhaar", is_enabled=False, is_required=False, display_order=13),
            SimpleNamespace(field_key="blood_group", is_enabled=True, is_required=True, display_order=11),
        ]),
        10,
    )
    with pytest.raises(HTTPException, match="disabled: aadhaar"):
        reject_disabled_student_fields(config, {"aadhaar"})
    values = {key: "present" for key in PROTECTED_STUDENT_FIELD_KEYS}
    with pytest.raises(HTTPException, match="Blood group is required"):
        validate_required_student_fields(config, values)
    values["blood_group"] = "A+"
    validate_required_student_fields(config, values)
