from typing import Any

from sqlalchemy.orm import Session

from app.core.student_audit import audit_value, is_sensitive_audit_field
from app.models.personnel import Personnel
from app.models.personnel_audit_event import PersonnelAuditEvent
from app.models.users import User


def record_personnel_audit(
    db: Session,
    *,
    personnel: Personnel,
    actor: User | None,
    event_type: str,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    note: str | None = None,
) -> PersonnelAuditEvent:
    if personnel.id is None:
        raise ValueError("Personnel record must be flushed before recording audit history")
    if is_sensitive_audit_field(field_name):
        raise ValueError("Sensitive fields cannot be written to personnel audit history")
    event = PersonnelAuditEvent(
        school_id=personnel.school_id,
        personnel_id=personnel.id,
        actor_user_id=actor.id if actor is not None else None,
        event_type=event_type,
        field_name=field_name,
        old_value=audit_value(old_value),
        new_value=audit_value(new_value),
        note=note,
    )
    db.add(event)
    return event


def record_personnel_field_changes(
    db: Session,
    *,
    personnel: Personnel,
    actor: User,
    changes: dict[str, tuple[Any, Any]],
) -> None:
    for field_name, (old_value, new_value) in changes.items():
        if audit_value(old_value) == audit_value(new_value):
            continue
        record_personnel_audit(
            db,
            personnel=personnel,
            actor=actor,
            event_type="personnel_field_updated",
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )
