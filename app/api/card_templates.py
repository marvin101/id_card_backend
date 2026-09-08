from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

_CONFLICT_DETAIL = (
    "This card template changed after it was loaded. Reload the latest version "
    "before saving again."
)


def _same_instant(left: datetime, right: datetime) -> bool:
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


def _next_updated_at(current: datetime) -> datetime:
    """Return a monotonic, microsecond-safe token for the next stored version."""
    current_utc = current.astimezone(timezone.utc)
    return max(datetime.now(timezone.utc), current_utc + timedelta(microseconds=1))


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
    # The row lock makes token comparison and mutation one atomic transaction.
    # The unique school_id constraint remains the final guard for concurrent
    # first-time creates, where no template row exists to lock yet.
    template = db.execute(
        select(CardTemplate)
        .where(CardTemplate.school_id == school.id)
        .with_for_update()
    ).scalar_one_or_none()
    if template is None:
        if template_data.expected_updated_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_CONFLICT_DETAIL,
            )
        template = CardTemplate(
            school_id=school.id,
            name=template_data.name.strip(),
            design=template_data.design,
        )
        db.add(template)
    else:
        if (
            template_data.expected_updated_at is not None
            and not _same_instant(
                template.updated_at, template_data.expected_updated_at
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_CONFLICT_DETAIL,
            )
        template.name = template_data.name.strip()
        template.design = template_data.design
        template.updated_at = _next_updated_at(template.updated_at)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from error
    db.refresh(template)
    return template
