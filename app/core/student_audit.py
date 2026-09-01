from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.student import Student
from app.models.student_audit_event import StudentAuditEvent
from app.models.users import User


SENSITIVE_FIELDS = frozenset({"password", "password_hash", "token", "secret"})


def is_sensitive_audit_field(field_name: str | None) -> bool:
    """Return whether an audit field path could contain authentication data."""
    if not field_name:
        return False
    parts = {
        part.casefold().replace("-", "_")
        for part in field_name.replace("[", ".").replace("]", "").split(".")
        if part
    }
    return any(
        part in SENSITIVE_FIELDS
        or "password" in part
        or "secret" in part
        or part.endswith("_token")
        for part in parts
    )


def audit_value(value: Any) -> Any:
    """Convert a model value into JSON-safe audit data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, list):
        return [audit_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): audit_value(item) for key, item in value.items()}
    return str(value)


def record_student_audit(
    db: Session,
    *,
    student: Student,
    actor: User,
    event_type: str,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    note: str | None = None,
) -> StudentAuditEvent:
    if student.id is None:
        raise ValueError("Student must be flushed before recording an audit event")
    if is_sensitive_audit_field(field_name):
        raise ValueError("Sensitive fields cannot be written to student audit history")
    event = StudentAuditEvent(
        school_id=student.school_id,
        student_id=student.id,
        actor_user_id=actor.id,
        event_type=event_type,
        field_name=field_name,
        old_value=audit_value(old_value),
        new_value=audit_value(new_value),
        note=note,
    )
    db.add(event)
    return event


def record_student_field_changes(
    db: Session,
    *,
    student: Student,
    actor: User,
    changes: dict[str, tuple[Any, Any]],
) -> None:
    for field_name, (old_value, new_value) in changes.items():
        if audit_value(old_value) == audit_value(new_value):
            continue
        record_student_audit(
            db,
            student=student,
            actor=actor,
            event_type="student_field_updated",
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )


def custom_field_change_set(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    """Build stable, non-noisy audit changes for student custom fields."""
    return {
        f"custom_fields.{field_key}": (
            before.get(field_key),
            after.get(field_key),
        )
        for field_key in before.keys() | after.keys()
        if audit_value(before.get(field_key)) != audit_value(after.get(field_key))
    }
