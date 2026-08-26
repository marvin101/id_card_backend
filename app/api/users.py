from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from uuid import UUID
from app.core.database import get_db
from app.core.security import get_current_user, hash_password
from app.core.school_access import (
    LEGACY_SCHOOL_ADMIN_ROLE,
    SCHOOL_ADMIN_ROLE,
    is_platform_admin,
    require_school_admin,
    require_school_role_management,
)
from app.models.users import User
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.schemas.auth import  (
    SchoolAccessCreate, 
    SchoolAccessResponse, 
    SchoolAccessUpdate, 
    SchoolUserAssignmentResponse,
    UserCreate, 
    UserResponse
    )
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
        designation=user_data.designation,
        platform_role=None,
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
# List Users and Their Assignment State for a School
# ==========================================================

@router.get(
    "/schools/{school_uuid}/assignments",
    response_model=list[SchoolUserAssignmentResponse],
    summary="List users and assignment status for a school",
    description=(
        "Lists every active user account for the selected school. Users with "
        "a school-access record include their role and are marked `assigned`; "
        "all other active users are marked `pending_assignment`. Platform "
        "administrators may view any school, while school administrators may "
        "view only schools they administer."
    ),
    responses={
        401: {"description": "Authentication is required."},
        403: {"description": "The user cannot view this school's assignments."},
        404: {"description": "School not found."},
    },
)
def list_school_user_assignments(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a school-scoped user directory without exposing other memberships."""
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

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can view assignments in this school",
    )

    result = db.execute(
        select(User, UserSchoolAccess.role)
        .outerjoin(
            UserSchoolAccess,
            (UserSchoolAccess.user_id == User.id)
            & (UserSchoolAccess.school_id == school.id),
        )
        .where(User.is_active.is_(True))
        .order_by(User.full_name, User.username)
    )

    return [
        SchoolUserAssignmentResponse(
            user_uuid=user.uuid,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            mobile=user.mobile,
            designation=user.designation,
            role=role,
            assignment_status="assigned" if role is not None else "pending_assignment",
        )
        for user, role in result.all()
    ]

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
    # Find school first so all authorization is evaluated against the exact
    # school represented by the URL.
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

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can grant access in this school",
        requested_role=access_data.role,
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
# ==========================================================
# List User's School Access
# ==========================================================

@router.get(
    "/{user_uuid}/schools",
    response_model=list[SchoolAccessResponse],
)
def list_user_school_access(
    user_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
    # Check permission
    # ------------------------------------------------------

    managed_school_ids: list[int] | None = None
    if current_user.id != user.id and not is_platform_admin(current_user):
        # School admins may inspect memberships only for schools they
        # administer. This permits local user management without exposing a
        # person's access at other schools.
        managed_school_ids = db.execute(
            select(UserSchoolAccess.school_id).where(
                UserSchoolAccess.user_id == current_user.id,
                UserSchoolAccess.role.in_(
                    {SCHOOL_ADMIN_ROLE, LEGACY_SCHOOL_ADMIN_ROLE}
                ),
            )
        ).scalars().all()

    # ------------------------------------------------------
    # Find school access records
    # ------------------------------------------------------

    statement = (
        select(
            UserSchoolAccess,
            School,
        )
        .join(
            School,
            School.id == UserSchoolAccess.school_id,
        )
        .where(
            UserSchoolAccess.user_id == user.id,
            School.is_active.is_(True),
        )
        .order_by(School.school_name)
    )

    if managed_school_ids is not None:
        statement = statement.where(
            UserSchoolAccess.school_id.in_(managed_school_ids)
        )

    result = db.execute(statement)

    # ------------------------------------------------------
    # Build response
    # ------------------------------------------------------

    return [
        SchoolAccessResponse(
            user_uuid=user.uuid,
            school_uuid=school.uuid,
            school_name=school.school_name,
            role=access.role,
        )
        for access, school in result.all()
    ]
# ==========================================================
# Update User School Access
# ==========================================================

@router.put(
    "/{user_uuid}/schools/{school_uuid}",
    response_model=SchoolAccessResponse,
)
def update_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    access_data: SchoolAccessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
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
    # Find existing access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User does not have access to this school",
        )

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can update access in this school",
        existing_role=access.role,
        requested_role=access_data.role,
    )

    # ------------------------------------------------------
    # Update role
    # ------------------------------------------------------

    access.role = access_data.role

    db.commit()
    db.refresh(access)

    return SchoolAccessResponse(
        user_uuid=user.uuid,
        school_uuid=school.uuid,
        school_name=school.school_name,
        role=access.role,
    )
# ==========================================================
# Revoke User School Access
# ==========================================================

@router.delete(
    "/{user_uuid}/schools/{school_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
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
    # Find existing access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User does not have access to this school",
        )

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can revoke access in this school",
        existing_role=access.role,
    )

    # ------------------------------------------------------
    # Revoke access
    # ------------------------------------------------------

    db.delete(access)
    db.commit()

    return None
