from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.school_access import get_active_school, require_card_data_access, require_school_admin
from app.core.security import get_current_user
from app.core.student_field_config import effective_student_fields
from app.models.student_field_config import SchoolStudentFieldConfig
from app.models.users import User
from app.schemas.student_field_config import BuiltinStudentFieldConfigResponse, BuiltinStudentFieldConfigWrite


router = APIRouter(prefix="/schools/{school_uuid}/student-field-config", tags=["Student Fields"])


@router.get("", response_model=BuiltinStudentFieldConfigResponse)
def get_student_field_config(school_uuid: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = get_active_school(db, school_uuid)
    require_card_data_access(db, current_user, school.id)
    return {"fields": effective_student_fields(db, school.id)}


@router.put("", response_model=BuiltinStudentFieldConfigResponse)
def put_student_field_config(
    school_uuid: UUID,
    payload: BuiltinStudentFieldConfigWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only a school administrator can manage student fields")
    existing = db.execute(select(SchoolStudentFieldConfig).where(SchoolStudentFieldConfig.school_id == school.id)).scalars().all()
    by_key = {row.field_key: row for row in existing}
    for item in payload.fields:
        row = by_key.get(item.key)
        if row is None:
            row = SchoolStudentFieldConfig(school_id=school.id, field_key=item.key)
            db.add(row)
        row.is_enabled = item.enabled
        row.is_required = item.required
        row.display_order = item.display_order
    db.commit()
    return {"fields": effective_student_fields(db, school.id)}
