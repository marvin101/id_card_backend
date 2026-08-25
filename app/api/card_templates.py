from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.school_access import get_active_school, require_school_access, require_school_admin
from app.core.security import get_current_user
from app.models.card_template import CardTemplate
from app.models.users import User
from app.schemas.card_template import CardTemplateResponse, CardTemplateUpdate


router = APIRouter(
    prefix="/schools/{school_uuid}/card-template",
    tags=["Card Templates"],
)


@router.get("", response_model=CardTemplateResponse)
def get_card_template(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_access(db, current_user, school.id)
    template = db.execute(
        select(CardTemplate).where(CardTemplate.school_id == school.id)
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This school does not have a card template yet",
        )
    return template


@router.put("", response_model=CardTemplateResponse)
def save_card_template(
    school_uuid: UUID,
    template_data: CardTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db, current_user, school.id,
        "Only a school or platform administrator can edit card templates",
    )
    template = db.execute(
        select(CardTemplate).where(CardTemplate.school_id == school.id)
    ).scalar_one_or_none()
    if template is None:
        template = CardTemplate(
            school_id=school.id,
            name=template_data.name.strip(),
            design=template_data.design,
        )
        db.add(template)
    else:
        template.name = template_data.name.strip()
        template.design = template_data.design

    db.commit()
    db.refresh(template)
    return template
