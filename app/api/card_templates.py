from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.file_storage import get_storage_public_url
from app.core.rate_limit import enforce_public_design_rate_limit
from app.core.school_access import get_active_school, require_school_access, require_school_admin
from app.core.security import get_current_user
from app.models.card_template import CardTemplate
from app.models.users import User
from app.schemas.card_template import (
    CardTemplateResponse,
    CardTemplateUpdate,
    PublicDesignSchool,
    PublicDesignShareResponse,
    PublicDesignShareUpdate,
    PublicDesignView,
)


router = APIRouter(
    prefix="/schools/{school_uuid}/card-template",
    tags=["Card Templates"],
)
public_router = APIRouter(prefix="/public/designs", tags=["Public Designs"])

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


def _school_template(db: Session, school_id: int) -> CardTemplate:
    template = db.execute(
        select(CardTemplate).where(CardTemplate.school_id == school_id)
    ).scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This school does not have a card template yet",
        )
    return template


@router.get("", response_model=CardTemplateResponse)
def get_card_template(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_access(db, current_user, school.id)
    return _school_template(db, school.id)


@router.get("/public-share", response_model=PublicDesignShareResponse)
def get_public_design_share(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school or platform administrator can manage the public design link",
    )
    template = _school_template(db, school.id)
    return PublicDesignShareResponse(
        enabled=template.public_enabled,
        public_token=template.public_token,
    )


@router.put("/public-share", response_model=PublicDesignShareResponse)
def update_public_design_share(
    school_uuid: UUID,
    payload: PublicDesignShareUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school or platform administrator can manage the public design link",
    )
    template = _school_template(db, school.id)
    if payload.enabled and template.public_token is None:
        template.public_token = secrets.token_urlsafe(32)
    template.public_enabled = payload.enabled
    db.commit()
    return PublicDesignShareResponse(
        enabled=template.public_enabled,
        public_token=template.public_token,
    )


@router.post(
    "/public-share/regenerate-link",
    response_model=PublicDesignShareResponse,
)
def regenerate_public_design_share(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school or platform administrator can manage the public design link",
    )
    template = _school_template(db, school.id)
    template.public_token = secrets.token_urlsafe(32)
    template.public_enabled = True
    db.commit()
    return PublicDesignShareResponse(
        enabled=True,
        public_token=template.public_token,
    )


@public_router.get("/{token}", response_model=PublicDesignView)
def get_public_design(
    token: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_public_design_rate_limit(request)
    response.headers["Cache-Control"] = "no-store"
    template = db.execute(
        select(CardTemplate).where(
            CardTemplate.public_token == token,
            CardTemplate.public_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if template is None or not template.school.is_active:
        raise HTTPException(status_code=404, detail="Public design not found")
    school = template.school
    return PublicDesignView(
        name=template.name,
        design=template.design,
        back_design=getattr(template, "back_design", None),
        school=PublicDesignSchool(
            uuid=school.uuid,
            school_code=school.school_code,
            school_name=school.school_name,
            email=school.email,
            phone=school.phone,
            website=school.website,
            address=school.address,
            city=school.city,
            district=school.district,
            state=school.state,
            country=school.country,
            postal_code=school.postal_code,
            principal_name=school.principal_name,
            logo_url=get_storage_public_url(school.logo_path),
        ),
    )


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
            back_design=template_data.back_design,
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
        if "back_design" in template_data.model_fields_set:
            template.back_design = template_data.back_design
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
