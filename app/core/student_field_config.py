from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.student_field_config import SchoolStudentFieldConfig


@dataclass(frozen=True)
class BuiltinStudentField:
    key: str
    label: str
    data_type: str
    protected: bool
    default_enabled: bool
    default_required: bool
    default_display_order: int


_FIELDS = (
    ("session_uuid", "Academic session", "select"),
    ("class_uuid", "Class", "select"),
    ("section_uuid", "Section", "select"),
    ("admission_no", "Admission number", "text"),
    ("full_name", "Full name", "text"),
    ("roll_no", "Roll number", "text"),
    ("stream", "Stream", "text"),
    ("father_name", "Father name", "text"),
    ("mother_name", "Mother name", "text"),
    ("dob", "Date of birth", "date"),
    ("gender", "Gender", "text"),
    ("blood_group", "Blood group", "select"),
    ("mobile", "Mobile", "phone"),
    ("aadhaar", "Aadhaar", "text"),
    ("address", "Address", "multiline"),
)
PROTECTED_STUDENT_FIELD_KEYS = frozenset({"session_uuid", "class_uuid", "section_uuid", "admission_no", "full_name"})
BUILTIN_STUDENT_FIELDS = {
    key: BuiltinStudentField(
        key=key,
        label=label,
        data_type=data_type,
        protected=key in PROTECTED_STUDENT_FIELD_KEYS,
        default_enabled=True,
        default_required=key in PROTECTED_STUDENT_FIELD_KEYS,
        default_display_order=index,
    )
    for index, (key, label, data_type) in enumerate(_FIELDS)
}


@dataclass(frozen=True)
class EffectiveStudentField:
    key: str
    label: str
    data_type: str
    protected: bool
    enabled: bool
    required: bool
    display_order: int


def effective_student_fields(db: Session, school_id: int) -> list[EffectiveStudentField]:
    # Lightweight endpoint unit tests use small protocol fakes rather than a
    # SQLAlchemy Session. They represent the backward-compatible no-override
    # state and therefore intentionally resolve to registry defaults.
    rows = (
        db.execute(
            select(SchoolStudentFieldConfig).where(
                SchoolStudentFieldConfig.school_id == school_id
            )
        ).scalars().all()
        if isinstance(db, Session)
        else []
    )
    overrides = {row.field_key: row for row in rows}
    result = []
    for field in BUILTIN_STUDENT_FIELDS.values():
        row = overrides.get(field.key)
        result.append(EffectiveStudentField(
            key=field.key,
            label=field.label,
            data_type=field.data_type,
            protected=field.protected,
            enabled=True if field.protected else (row.is_enabled if row else field.default_enabled),
            required=True if field.protected else (row.is_required if row else field.default_required),
            display_order=row.display_order if row else field.default_display_order,
        ))
    return sorted(result, key=lambda item: (item.display_order, item.key))


def effective_student_field_map(db: Session, school_id: int) -> dict[str, EffectiveStudentField]:
    return {field.key: field for field in effective_student_fields(db, school_id)}


def reject_disabled_student_fields(config: dict[str, EffectiveStudentField], supplied: Iterable[str]) -> None:
    disabled = sorted(key for key in supplied if key in config and not config[key].enabled)
    if disabled:
        raise HTTPException(status_code=422, detail=f"Built-in student field is disabled: {disabled[0]}")


def validate_required_student_fields(config: dict[str, EffectiveStudentField], values: dict[str, Any]) -> None:
    for field in sorted(config.values(), key=lambda item: (item.display_order, item.key)):
        if not field.enabled or not field.required:
            continue
        value = values.get(field.key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise HTTPException(status_code=422, detail=f"{field.label} is required")
