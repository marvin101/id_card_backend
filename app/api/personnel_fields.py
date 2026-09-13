from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.school_access import get_active_school, require_card_data_access, require_school_admin
from app.core.security import get_current_user
from app.models.custom_field import CustomFieldDefinition
from app.models.users import User
from app.schemas.custom_field import (
    StudentFieldDefinitionCreate,
    StudentFieldDefinitionResponse,
    StudentFieldDefinitionUpdate,
    StudentFieldReorderRequest,
)
from app.schemas.personnel import PersonnelType


router = APIRouter(
    prefix="/schools/{school_uuid}/personnel-fields",
    tags=["Personnel Fields"],
)


def _entity_type(value: PersonnelType) -> str:
    return value.value


@router.get("", response_model=list[StudentFieldDefinitionResponse])
def list_personnel_fields(
    school_uuid: UUID,
    personnel_type: PersonnelType,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_card_data_access(db, current_user, school.id)
    query = select(CustomFieldDefinition).where(
        CustomFieldDefinition.school_id == school.id,
        CustomFieldDefinition.entity_type == _entity_type(personnel_type),
    )
    if not include_inactive:
        query = query.where(CustomFieldDefinition.is_active.is_(True))
    return db.execute(query.order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)).scalars().all()


@router.post("", response_model=StudentFieldDefinitionResponse, status_code=201)
def create_personnel_field(
    school_uuid: UUID,
    personnel_type: PersonnelType,
    payload: StudentFieldDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only a school administrator can manage personnel fields")
    entity_type = _entity_type(personnel_type)
    order = payload.display_order
    if order is None:
        order = db.execute(select(func.coalesce(func.max(CustomFieldDefinition.display_order), -1)).where(
            CustomFieldDefinition.school_id == school.id,
            CustomFieldDefinition.entity_type == entity_type,
        )).scalar_one() + 1
    definition = CustomFieldDefinition(
        school_id=school.id,
        entity_type=entity_type,
        field_key=payload.field_key,
        label=payload.label,
        data_type=payload.data_type.value,
        is_required=payload.is_required,
        display_order=order,
    )
    db.add(definition)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Field key already exists for this personnel type") from exc
    db.refresh(definition)
    return definition


@router.patch("/{field_uuid}", response_model=StudentFieldDefinitionResponse)
def update_personnel_field(
    school_uuid: UUID,
    field_uuid: UUID,
    personnel_type: PersonnelType,
    payload: StudentFieldDefinitionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only a school administrator can manage personnel fields")
    definition = db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.uuid == field_uuid,
        CustomFieldDefinition.school_id == school.id,
        CustomFieldDefinition.entity_type == _entity_type(personnel_type),
    )).scalar_one_or_none()
    if definition is None:
        raise HTTPException(status_code=404, detail="Personnel field not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(definition, key, value.value if hasattr(value, "value") else value)
    db.commit()
    db.refresh(definition)
    return definition


@router.put("/reorder", response_model=list[StudentFieldDefinitionResponse])
def reorder_personnel_fields(
    school_uuid: UUID,
    personnel_type: PersonnelType,
    payload: StudentFieldReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only a school administrator can manage personnel fields")
    entity_type = _entity_type(personnel_type)
    uuids = [item.field_uuid for item in payload.fields]
    if len(uuids) != len(set(uuids)):
        raise HTTPException(status_code=422, detail="Duplicate field UUID in reorder request")
    definitions = db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.school_id == school.id,
        CustomFieldDefinition.entity_type == entity_type,
        CustomFieldDefinition.uuid.in_(uuids),
    )).scalars().all() if uuids else []
    by_uuid = {item.uuid: item for item in definitions}
    if len(by_uuid) != len(uuids):
        raise HTTPException(status_code=404, detail="Personnel field not found")
    for item in payload.fields:
        by_uuid[item.field_uuid].display_order = item.display_order
    db.commit()
    return db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.school_id == school.id,
        CustomFieldDefinition.entity_type == entity_type,
    ).order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)).scalars().all()
