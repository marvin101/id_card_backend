import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.file_storage import (
    MAX_SCHOOL_LOGO_SIZE,
    StorageError,
    delete_storage_object,
    get_storage_public_url,
    save_school_logo,
    save_principal_signature,
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
from app.schemas.school import SchoolCreate, SchoolResponse, SchoolUpdate, SchoolActivation


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
)


def _school_response(school: School) -> SchoolResponse:
    response = SchoolResponse.model_validate(school)
    return response.model_copy(
        update={"logo_url": get_storage_public_url(school.logo_path),
                "principal_signature_url": get_storage_public_url(school.principal_signature_path)}
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
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if include_inactive:
        require_platform_admin(current_user)
    # Platform admins can see all active schools.
    if is_platform_admin(current_user):
        result = db.execute(
            select(School)
            .where(
                School.is_active.is_(True) if not include_inactive else True,
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

    content = await logo.read(MAX_SCHOOL_LOGO_SIZE + 1)
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



@router.post("/{school_uuid}/principal-signature", response_model=SchoolResponse)
async def upload_principal_signature(
    school_uuid: UUID, signature: UploadFile = File(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only an administrator can update the principal signature")
    content = await signature.read(MAX_SCHOOL_LOGO_SIZE + 1)
    old_path = school.principal_signature_path
    try:
        new_path = save_principal_signature(school.uuid, content, signature.content_type, signature.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=502, detail="Signature storage is currently unavailable.") from exc
    school.principal_signature_path = new_path
    try:
        db.commit()
        db.refresh(school)
    except Exception:
        db.rollback()
        try:
            delete_storage_object(new_path)
        except StorageError:
            logger.warning("Could not clean up newly uploaded signature", exc_info=True)
        raise
    if old_path and old_path != new_path and old_path.startswith(f"schools/{school.uuid}/signatures/"):
        try:
            delete_storage_object(old_path)
        except StorageError:
            logger.warning("Could not remove replaced signature", exc_info=True)
    return _school_response(school)


@router.delete("/{school_uuid}/principal-signature", response_model=SchoolResponse)
def remove_principal_signature(
    school_uuid: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only an administrator can remove the principal signature")
    old_path = school.principal_signature_path
    school.principal_signature_path = None
    db.commit()
    db.refresh(school)
    if old_path and old_path.startswith(f"schools/{school.uuid}/signatures/"):
        try:
            delete_storage_object(old_path)
        except StorageError:
            logger.warning("Signature detached; object cleanup failed", exc_info=True)
    return _school_response(school)


@router.patch("/{school_uuid}/activation", response_model=SchoolResponse)
def set_school_activation(school_uuid: UUID, data: SchoolActivation,
                          db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    school = db.execute(select(School).where(School.uuid == school_uuid).with_for_update()).scalar_one_or_none()
    if school is None:
        raise HTTPException(404, "School not found")
    school.is_active = data.is_active
    db.commit()
    db.refresh(school)
    return _school_response(school)


@router.delete("/{school_uuid}/logo", response_model=SchoolResponse)
def remove_school_logo(school_uuid: UUID, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, "Only an administrator can remove the school logo")
    old_path = school.logo_path
    school.logo_path = None
    db.commit()
    db.refresh(school)
    if old_path and old_path.startswith(f"schools/{school.uuid}/logos/"):
        try:
            delete_storage_object(old_path)
        except StorageError:
            logger.warning("School logo detached; previous object cleanup failed")
    return _school_response(school)
