import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.file_storage import (
    StorageError,
    delete_storage_object,
    get_storage_public_url,
    save_school_logo,
)
from app.core.security import get_current_user
from app.core.school_access import (
    get_active_school,
    is_platform_admin,
    require_platform_admin,
    require_school_access,
    require_school_admin,
)
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User
from app.schemas.school import SchoolCreate, SchoolResponse, SchoolUpdate


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
)


def _school_response(school: School) -> SchoolResponse:
    response = SchoolResponse.model_validate(school)
    return response.model_copy(
        update={"logo_url": get_storage_public_url(school.logo_path)}
    )


# ==========================================================
# Create School
# ==========================================================

@router.post(
    "",
    response_model=SchoolResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_school(
    school_data: SchoolCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_platform_admin(current_user)
    existing_school = db.execute(
        select(School).where(
            School.school_code == school_data.school_code
        )
    ).scalar_one_or_none()

    if existing_school is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="School code already exists",
        )

    school = School(
        **school_data.model_dump()
    )

    db.add(school)
    db.flush()

    db.commit()
    db.refresh(school)

    return _school_response(school)


# ==========================================================
# List My Schools
# ==========================================================

@router.get(
    "",
    response_model=list[SchoolResponse],
)
def list_my_schools(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Platform admins can see all active schools.
    if is_platform_admin(current_user):
        result = db.execute(
            select(School)
            .where(
                School.is_active.is_(True),
            )
            .order_by(School.school_name)
        )

        return [_school_response(school) for school in result.scalars().all()]

    # Normal users can see only schools they have access to.
    result = db.execute(
        select(School)
        .join(
            UserSchoolAccess,
            UserSchoolAccess.school_id == School.id,
        )
        .where(
            UserSchoolAccess.user_id == current_user.id,
            School.is_active.is_(True),
        )
        .order_by(School.school_name)
    )

    return [_school_response(school) for school in result.scalars().all()]


@router.get(
    "/{school_uuid}/profile",
    response_model=SchoolResponse,
)
def get_school_profile(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_access(db, current_user, school.id)
    return _school_response(school)


@router.patch(
    "/{school_uuid}/profile",
    response_model=SchoolResponse,
)
def update_school_profile(
    school_uuid: UUID,
    profile_data: SchoolUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a platform or school administrator can update the school profile",
    )

    for field, value in profile_data.model_dump(exclude_unset=True).items():
        setattr(school, field, value)

    db.commit()
    db.refresh(school)
    return _school_response(school)


@router.post(
    "/{school_uuid}/logo",
    response_model=SchoolResponse,
)
async def upload_school_logo(
    school_uuid: UUID,
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a platform or school administrator can update the school logo",
    )

    content = await logo.read()
    old_logo_path = school.logo_path

    try:
        new_logo_path = save_school_logo(
            school.uuid,
            content,
            logo.content_type,
            logo.filename,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="School logo storage is currently unavailable.",
        ) from exc

    school.logo_path = new_logo_path
    try:
        db.commit()
        db.refresh(school)
    except Exception:
        db.rollback()
        try:
            delete_storage_object(new_logo_path)
        except StorageError:
            logger.warning(
                "Could not clean up newly uploaded school logo after DB failure",
                exc_info=True,
            )
        raise

    expected_prefix = f"schools/{school.uuid}/logos/"
    if (
        old_logo_path
        and old_logo_path != new_logo_path
        and old_logo_path.startswith(expected_prefix)
    ):
        try:
            delete_storage_object(old_logo_path)
        except StorageError:
            logger.warning(
                "School logo was replaced but the previous object could not be removed",
                exc_info=True,
            )

    return _school_response(school)
