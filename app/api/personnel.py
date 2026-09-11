from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.personnel_audit import (
    record_personnel_audit,
    record_personnel_field_changes,
)
from app.core.school_access import (
    get_active_school,
    require_identity_data_access,
    require_school_admin,
)
from app.core.security import get_current_user
from app.models.custom_field import PersonnelCustomFieldValue
from app.models.personnel import Personnel
from app.models.personnel_audit_event import PersonnelAuditEvent
from app.models.users import User
from app.schemas.personnel import (
    PersonnelAuditEventResponse,
    PersonnelBatchRequest,
    PersonnelBatchResult,
    PersonnelCreate,
    PersonnelPageResponse,
    PersonnelResponse,
    PersonnelType,
    PersonnelUpdate,
    PersonnelVerificationUpdate,
)
from app.schemas.student import VerificationStatus


router = APIRouter(
    prefix="/schools/{school_uuid}/personnel",
    tags=["Personnel"],
)

_BATCH_VERIFIABLE_STATUSES = frozenset(
    {VerificationStatus.PENDING.value, VerificationStatus.NEEDS_CORRECTION.value}
)
_MUTABLE_FIELDS = (
    "personnel_type",
    "employee_no",
    "full_name",
    "designation",
    "department",
    "dob",
    "gender",
    "blood_group",
    "mobile",
    "email",
    "address",
)


def _response_options():
    return (
        selectinload(Personnel.linked_user),
        selectinload(Personnel.verified_by),
        selectinload(Personnel.printed_by),
        selectinload(Personnel.custom_field_values).selectinload(
            PersonnelCustomFieldValue.field_definition
        ),
    )


def _active_personnel(
    db: Session, school_id: int, personnel_uuid: UUID
) -> Personnel:
    value = db.execute(
        select(Personnel)
        .options(*_response_options())
        .where(
            Personnel.uuid == personnel_uuid,
            Personnel.school_id == school_id,
            Personnel.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if value is None:
        raise HTTPException(status_code=404, detail="Personnel record not found")
    return value


def _ensure_employee_no_available(
    db: Session,
    *,
    school_id: int,
    employee_no: str,
    exclude_id: int | None = None,
) -> None:
    query = select(Personnel.id).where(
        Personnel.school_id == school_id,
        Personnel.employee_no == employee_no,
    )
    if exclude_id is not None:
        query = query.where(Personnel.id != exclude_id)
    if db.execute(query).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee number already exists in this school",
        )


def _apply_verification_transition(
    db: Session,
    *,
    personnel: Personnel,
    actor: User,
    target_status: VerificationStatus,
    note: str | None,
    verified_at: datetime | None = None,
) -> bool:
    old_status = personnel.verification_status
    old_note = personnel.correction_note
    target_note = note if target_status == VerificationStatus.NEEDS_CORRECTION else None
    if old_status == target_status.value and old_note == target_note:
        return False

    personnel.verification_status = target_status.value
    personnel.correction_note = target_note
    if target_status == VerificationStatus.VERIFIED:
        if old_status != VerificationStatus.VERIFIED.value:
            personnel.verified_at = verified_at or datetime.now(timezone.utc)
            personnel.verified_by_user_id = actor.id
    else:
        personnel.verified_at = None
        personnel.verified_by_user_id = None

    if old_status != personnel.verification_status:
        record_personnel_audit(
            db,
            personnel=personnel,
            actor=actor,
            event_type="verification_status_changed",
            field_name="verification_status",
            old_value=old_status,
            new_value=personnel.verification_status,
            note=target_note,
        )
    if old_note != personnel.correction_note:
        record_personnel_audit(
            db,
            personnel=personnel,
            actor=actor,
            event_type="correction_note_changed",
            field_name="correction_note",
            old_value=old_note,
            new_value=personnel.correction_note,
            note=target_note,
        )
    return True


@router.post("", response_model=PersonnelResponse, status_code=status.HTTP_201_CREATED)
def create_personnel(
    school_uuid: UUID,
    payload: PersonnelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can create personnel records",
    )
    _ensure_employee_no_available(
        db, school_id=school.id, employee_no=payload.employee_no
    )
    values = payload.model_dump()
    values["personnel_type"] = payload.personnel_type.value
    values["blood_group"] = payload.blood_group.value if payload.blood_group else None
    personnel = Personnel(school_id=school.id, **values)
    db.add(personnel)
    db.flush()
    record_personnel_audit(
        db,
        personnel=personnel,
        actor=current_user,
        event_type="personnel_created",
        new_value={
            "personnel_type": personnel.personnel_type,
            "employee_no": personnel.employee_no,
            "full_name": personnel.full_name,
        },
    )
    db.commit()
    db.refresh(personnel)
    return personnel


@router.get("", response_model=PersonnelPageResponse)
def list_personnel(
    school_uuid: UUID,
    personnel_type: PersonnelType | None = None,
    search: str | None = Query(default=None, max_length=150),
    verification_status: VerificationStatus | None = None,
    printed: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(db, current_user, school.id)
    filters = [Personnel.school_id == school.id, Personnel.is_active.is_(True)]
    if personnel_type is not None:
        filters.append(Personnel.personnel_type == personnel_type.value)
    if verification_status is not None:
        filters.append(Personnel.verification_status == verification_status.value)
    if printed is not None:
        filters.append(Personnel.print_count > 0 if printed else Personnel.print_count == 0)
    normalized_search = search.strip() if search else None
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                Personnel.full_name.ilike(pattern),
                Personnel.employee_no.ilike(pattern),
                Personnel.designation.ilike(pattern),
                Personnel.department.ilike(pattern),
            )
        )

    total = db.execute(
        select(func.count(Personnel.id)).where(*filters)
    ).scalar_one()
    items = db.execute(
        select(Personnel)
        .options(*_response_options())
        .where(*filters)
        .order_by(Personnel.full_name, Personnel.id)
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return PersonnelPageResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )


@router.post("/batch-verify", response_model=PersonnelBatchResult)
def batch_verify_personnel(
    school_uuid: UUID,
    payload: PersonnelBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db, current_user, school.id, "Only a school administrator can verify personnel"
    )
    unique_uuids = list(dict.fromkeys(payload.personnel_uuids))
    records = db.execute(
        select(Personnel).where(
            Personnel.school_id == school.id,
            Personnel.uuid.in_(unique_uuids),
            Personnel.is_active.is_(True),
        )
    ).scalars().all()
    if len(records) != len(unique_uuids):
        raise HTTPException(status_code=404, detail="One or more personnel records were not found")
    if any(record.verification_status not in _BATCH_VERIFIABLE_STATUSES for record in records):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Batch verify accepts only Pending or Needs Correction personnel records",
        )
    now = datetime.now(timezone.utc)
    for record in records:
        _apply_verification_transition(
            db,
            personnel=record,
            actor=current_user,
            target_status=VerificationStatus.VERIFIED,
            note=None,
            verified_at=now,
        )
    db.commit()
    return PersonnelBatchResult(updated_count=len(records), personnel=records)


@router.post("/batch-mark-printed", response_model=PersonnelBatchResult)
def batch_mark_personnel_printed(
    school_uuid: UUID,
    payload: PersonnelBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db, current_user, school.id, "Only an authorized print role can mark cards printed"
    )
    unique_uuids = list(dict.fromkeys(payload.personnel_uuids))
    records = db.execute(
        select(Personnel).where(
            Personnel.school_id == school.id,
            Personnel.uuid.in_(unique_uuids),
            Personnel.is_active.is_(True),
        )
    ).scalars().all()
    if len(records) != len(unique_uuids):
        raise HTTPException(status_code=404, detail="One or more personnel records were not found")
    if any(record.verification_status != VerificationStatus.VERIFIED.value for record in records):
        raise HTTPException(status_code=409, detail="Only verified personnel can be marked printed")
    now = datetime.now(timezone.utc)
    for record in records:
        old_count = record.print_count
        record.print_count = old_count + 1
        record.printed_at = now
        record.printed_by_user_id = current_user.id
        record_personnel_audit(
            db,
            personnel=record,
            actor=current_user,
            event_type="reprinted" if old_count else "marked_printed",
            field_name="print_count",
            old_value=old_count,
            new_value=record.print_count,
        )
    db.commit()
    return PersonnelBatchResult(updated_count=len(records), personnel=records)


@router.get("/{personnel_uuid}", response_model=PersonnelResponse)
def get_personnel(
    school_uuid: UUID,
    personnel_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(db, current_user, school.id)
    return _active_personnel(db, school.id, personnel_uuid)


@router.put("/{personnel_uuid}", response_model=PersonnelResponse)
def update_personnel(
    school_uuid: UUID,
    personnel_uuid: UUID,
    payload: PersonnelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can update personnel records",
    )
    personnel = _active_personnel(db, school.id, personnel_uuid)
    values = payload.model_dump(exclude_unset=True)
    if values.get("personnel_type") is not None:
        values["personnel_type"] = values["personnel_type"].value
    if values.get("blood_group") is not None:
        values["blood_group"] = values["blood_group"].value
    if "employee_no" in values:
        _ensure_employee_no_available(
            db,
            school_id=school.id,
            employee_no=values["employee_no"],
            exclude_id=personnel.id,
        )
    changes = {
        field: (getattr(personnel, field), values[field])
        for field in _MUTABLE_FIELDS
        if field in values
    }
    for field, value in values.items():
        setattr(personnel, field, value)
    record_personnel_field_changes(
        db, personnel=personnel, actor=current_user, changes=changes
    )
    if not any(old != new for old, new in changes.values()):
        return personnel
    db.commit()
    db.refresh(personnel)
    return personnel


@router.patch("/{personnel_uuid}/verification", response_model=PersonnelResponse)
def update_personnel_verification(
    school_uuid: UUID,
    personnel_uuid: UUID,
    payload: PersonnelVerificationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can change personnel verification status",
    )
    personnel = _active_personnel(db, school.id, personnel_uuid)
    note = payload.note.strip() if payload.note else None
    if payload.status == VerificationStatus.NEEDS_CORRECTION and not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A correction note is required when marking Needs Correction",
        )
    changed = _apply_verification_transition(
        db,
        personnel=personnel,
        actor=current_user,
        target_status=payload.status,
        note=note,
    )
    if changed:
        db.commit()
        db.refresh(personnel)
    return personnel


@router.post("/{personnel_uuid}/mark-printed", response_model=PersonnelResponse)
def mark_personnel_printed(
    school_uuid: UUID,
    personnel_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db, current_user, school.id, "Only an authorized print role can mark cards printed"
    )
    personnel = _active_personnel(db, school.id, personnel_uuid)
    if personnel.verification_status != VerificationStatus.VERIFIED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only verified personnel can be marked printed",
        )
    old_count = personnel.print_count
    personnel.print_count = old_count + 1
    personnel.printed_at = datetime.now(timezone.utc)
    personnel.printed_by_user_id = current_user.id
    record_personnel_audit(
        db,
        personnel=personnel,
        actor=current_user,
        event_type="reprinted" if old_count else "marked_printed",
        field_name="print_count",
        old_value=old_count,
        new_value=personnel.print_count,
    )
    db.commit()
    db.refresh(personnel)
    return personnel


@router.get(
    "/{personnel_uuid}/history", response_model=list[PersonnelAuditEventResponse]
)
def get_personnel_history(
    school_uuid: UUID,
    personnel_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db, current_user, school.id, "Only a school administrator can view personnel history"
    )
    personnel = _active_personnel(db, school.id, personnel_uuid)
    return db.execute(
        select(PersonnelAuditEvent)
        .options(selectinload(PersonnelAuditEvent.actor))
        .where(
            PersonnelAuditEvent.school_id == school.id,
            PersonnelAuditEvent.personnel_id == personnel.id,
        )
        .order_by(
            PersonnelAuditEvent.created_at.desc(), PersonnelAuditEvent.id.desc()
        )
    ).scalars().all()


@router.delete("/{personnel_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_personnel(
    school_uuid: UUID,
    personnel_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db, current_user, school.id, "Only a school administrator can delete personnel"
    )
    personnel = _active_personnel(db, school.id, personnel_uuid)
    personnel.is_active = False
    record_personnel_audit(
        db,
        personnel=personnel,
        actor=current_user,
        event_type="personnel_deactivated",
        field_name="is_active",
        old_value=True,
        new_value=False,
    )
    db.commit()
    return None
