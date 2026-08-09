from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from uuid import UUID
from app.core.database import get_db
from app.core.security import get_current_user, hash_password
from app.models.users import User
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.schemas.auth import  SchoolAccessCreate, UserCreate, UserResponse

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    # ------------------------------------------------------
    # Check whether username already exists
    # ------------------------------------------------------

    existing_user = db.scalar(
        select(User).where(User.username == user_data.username)
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists.",
        )

    # ------------------------------------------------------
    # Create user
    # ------------------------------------------------------

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        email=user_data.email,
        mobile=user_data.mobile,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@router.get(
    "/me",
    response_model=UserResponse,
)
def get_my_profile(
    current_user: User = Depends(get_current_user),
):
    return current_user

# ==========================================================
# Grant User Access to School
# ==========================================================

@router.post(
    "/{user_uuid}/schools/{school_uuid}",
    status_code=status.HTTP_201_CREATED,
)
def grant_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    access_data: SchoolAccessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Check platform admin permission
    # ------------------------------------------------------

    if not current_user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator can grant school access",
        )

    # ------------------------------------------------------
    # Find target user
    # ------------------------------------------------------

    user = db.execute(
        select(User).where(
            User.uuid == user_uuid,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ------------------------------------------------------
    # Find school
    # ------------------------------------------------------

    school = db.execute(
        select(School).where(
            School.uuid == school_uuid,
            School.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    # ------------------------------------------------------
    # Check existing access
    # ------------------------------------------------------

    existing_access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if existing_access is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has access to this school",
        )

    # ------------------------------------------------------
    # Create access
    # ------------------------------------------------------

    access = UserSchoolAccess(
        user_id=user.id,
        school_id=school.id,
        role=access_data.role,
    )

    db.add(access)
    db.commit()
    db.refresh(access)

    return {
        "user_uuid": user.uuid,
        "school_uuid": school.uuid,
        "role": access.role,
    }