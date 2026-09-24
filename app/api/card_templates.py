from datetime import datetime, timedelta, timezone
from copy import deepcopy
import secrets
from typing import Any
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
from app.models.custom_field import CustomFieldDefinition
from app.models.users import User
from app.schemas.card_template import (
    CardTemplateCopyError,
    CardTemplateCopyRequest,
    CardTemplateResponse,
    CardTemplateUpdate,
    PublicDesignSchool,
    PublicDesignShareResponse,
    PublicDesignShareUpdate,
    PublicDesignView,
    UnresolvedCustomFieldBinding,
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


def _custom_field_references(
    document: dict[str, Any] | None,
    side: str,
) -> dict[UUID, list[str]]:
    references: dict[UUID, list[str]] = {}
    if not document or document.get("schema_version", document.get("version", 1)) != 2:
        return references
    for element_index, element in enumerate(document.get("elements", [])):
        if not isinstance(element, dict):
            continue
        data = element.get("data")
        if not isinstance(data, dict):
            continue
        value = data.get("field_uuid")
        if isinstance(value, str):
            try:
                references.setdefault(UUID(value), []).append(
                    f"{side}.elements[{element_index}].data.field_uuid"
                )
            except ValueError:
                # Stored v2 documents have already passed schema validation.
                pass
        fields = data.get("fields")
        if isinstance(fields, list):
            for field_index, binding in enumerate(fields):
                if not isinstance(binding, dict):
                    continue
                value = binding.get("field_uuid")
                if isinstance(value, str):
                    try:
                        references.setdefault(UUID(value), []).append(
                            f"{side}.elements[{element_index}].data.fields[{field_index}].field_uuid"
                        )
                    except ValueError:
                        pass
    return references


def _replace_custom_field_uuids(
    document: dict[str, Any] | None,
    replacements: dict[str, str],
) -> dict[str, Any] | None:
    copied = deepcopy(document)
    if not copied:
        return copied
    for element in copied.get("elements", []):
        data = element.get("data") if isinstance(element, dict) else None
        if not isinstance(data, dict):
            continue
        value = data.get("field_uuid")
        if value in replacements:
            data["field_uuid"] = replacements[value]
        for binding in data.get("fields", []):
            if isinstance(binding, dict) and binding.get("field_uuid") in replacements:
                binding["field_uuid"] = replacements[binding["field_uuid"]]
    return copied


def _custom_field_copy_mapping(
    db: Session,
    source_school_id: int,
    target_school_id: int,
    design: dict[str, Any],
    back_design: dict[str, Any] | None,
) -> tuple[dict[str, str], list[UnresolvedCustomFieldBinding]]:
    references = _custom_field_references(design, "front")
    for field_uuid, locations in _custom_field_references(back_design, "back").items():
        references.setdefault(field_uuid, []).extend(locations)
    if not references:
        return {}, []

    source_fields = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.school_id == source_school_id,
            CustomFieldDefinition.uuid.in_(references),
        )
    ).scalars().all()
    source_by_uuid = {field.uuid: field for field in source_fields}
    destination_fields = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.school_id == target_school_id,
        )
    ).scalars().all()
    destination_by_identity = {
        (field.entity_type, field.field_key): field for field in destination_fields
    }

    replacements: dict[str, str] = {}
    unresolved: list[UnresolvedCustomFieldBinding] = []
    for field_uuid, locations in references.items():
        source = source_by_uuid.get(field_uuid)
        destination = (
            destination_by_identity.get((source.entity_type, source.field_key))
            if source is not None
            else None
        )
        reason: str | None = None
        if source is None:
            reason = "The source custom field no longer exists in the source school."
        elif destination is None:
            reason = "No destination custom field has the same entity type and field key."
        elif destination.data_type != source.data_type:
            reason = "The destination custom field has an incompatible data type."
        elif not destination.is_active:
            reason = "The matching destination custom field is inactive."

        if reason is None:
            replacements[str(field_uuid)] = str(destination.uuid)
        else:
            unresolved.append(
                UnresolvedCustomFieldBinding(
                    field_uuid=field_uuid,
                    field_key=source.field_key if source else None,
                    label=source.label if source else None,
                    entity_type=source.entity_type if source else None,
                    data_type=source.data_type if source else None,
                    reason=reason,
                    locations=locations,
                )
            )
    return replacements, unresolved


@router.get("", response_model=CardTemplateResponse)
def get_card_template(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_access(db, current_user, school.id)
    return _school_template(db, school.id)


@router.post("/copy", response_model=CardTemplateResponse)
def copy_card_template(
    school_uuid: UUID,
    payload: CardTemplateCopyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_school = get_active_school(db, school_uuid)
    source_school = get_active_school(db, payload.source_school_uuid)
    if source_school.id == target_school.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and destination schools must be different",
        )

    require_school_access(db, current_user, source_school.id)
    require_school_admin(
        db,
        current_user,
        target_school.id,
        "Only a school or platform administrator can edit card templates",
    )
    source_template = _school_template(db, source_school.id)

    # Lock an existing destination before checking its version and changing it.
    target_template = db.execute(
        select(CardTemplate)
        .where(CardTemplate.school_id == target_school.id)
        .with_for_update()
    ).scalar_one_or_none()
    if target_template is None:
        if payload.expected_updated_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_CONFLICT_DETAIL,
            )
    elif (
        payload.expected_updated_at is None
        or not _same_instant(target_template.updated_at, payload.expected_updated_at)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        )

    replacements, unresolved = _custom_field_copy_mapping(
        db,
        source_school.id,
        target_school.id,
        source_template.design,
        source_template.back_design,
    )
    if unresolved:
        error = CardTemplateCopyError(
            message=(
                "Some fields in this design do not exist in the destination school."
            ),
            unresolved_fields=unresolved,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.model_dump(mode="json"),
        )

    copied_design = _replace_custom_field_uuids(
        source_template.design, replacements
    )
    copied_back_design = _replace_custom_field_uuids(
        source_template.back_design, replacements
    )
    if target_template is None:
        target_template = CardTemplate(
            school_id=target_school.id,
            name=source_template.name,
            design=copied_design,
            back_design=copied_back_design,
        )
        db.add(target_template)
    else:
        target_template.name = source_template.name
        target_template.design = copied_design
        target_template.back_design = copied_back_design
        target_template.updated_at = _next_updated_at(target_template.updated_at)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from error
    db.refresh(target_template)
    return target_template


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
            principal_signature_url=get_storage_public_url(school.principal_signature_path),
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
