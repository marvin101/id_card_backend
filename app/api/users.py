from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from uuid import UUID
from app.core.database import get_db
from app.core.rate_limit import enforce_registration_rate_limit
from app.core.security import get_current_user, hash_password
from app.core.school_access import (
    LEGACY_SCHOOL_ADMIN_ROLE,
    SCHOOL_ADMIN_ROLE,
    is_platform_admin,
    require_platform_admin,
    require_school_admin,
    require_school_role_management,
)
from app.models.users import User
from app.models.school import School
from app.models.school_access_request import SchoolAccessRequest
from app.models.user_school_access import UserSchoolAccess
from app.schemas.auth import  (
    SchoolAccessCreate, 
    SchoolAccessResponse, 
    SchoolAccessUpdate, 
    SchoolUserAssignmentResponse,
    RegistrationSchoolResponse,
    UserCreate, 
    UserResponse, AdminUserCreate, AdminUserUpdate
    )
router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


def _resolve_registration_school(db: Session, user_data: UserCreate) -> School:
    """Resolve a registration request to exactly one active school."""
    if user_data.school_uuid is not None:
        school = db.execute(
            select(School).where(
                School.uuid == user_data.school_uuid,
                School.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if school is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The selected school is no longer available.",
            )
        return school

    # Retain exact-name lookup temporarily so an already-deployed frontend can
    # continue registering while the backend-first rollout is in progress.
    assert user_data.school_name is not None
    normalized_school_name = user_data.school_name.strip()
    schools = db.execute(
        select(School).where(
            func.lower(School.school_name) == normalized_school_name.lower(),
            School.is_active.is_(True),
        )
    ).scalars().all()

    if not schools:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active school matches that name.",
        )

    if len(schools) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Multiple schools share that name. Contact the platform "
                "administrator before registering."
            ),
        )

    return schools[0]


@router.get(
    "/registration-schools",
    response_model=list[RegistrationSchoolResponse],
    summary="List active schools available during registration",
)
def list_registration_schools(
    db: Session = Depends(get_db),
):
    return db.execute(
        select(School)
        .where(School.is_active.is_(True))
        .order_by(School.school_name, School.uuid)
    ).scalars().all()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user_data: UserCreate,
    _: None = Depends(enforce_registration_rate_limit),
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

    school = _resolve_registration_school(db, user_data)

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        email=user_data.email,
        mobile=user_data.mobile,
        designation=user_data.designation.strip(),
        platform_role=None,
    )

    db.add(user)
    db.flush()
    db.add(
        SchoolAccessRequest(
            user_id=user.id,
            school_id=school.id,
            status="pending",
        )
    )
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
        "Lists active users assigned to the selected school plus users with "
        "a pending access request for that school. Platform "
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

    assigned_rows = db.execute(
        select(User, UserSchoolAccess.role)
        .join(
            UserSchoolAccess,
            (UserSchoolAccess.user_id == User.id)
            & (UserSchoolAccess.school_id == school.id),
        )
        .where(User.is_active.is_(True))
    ).all()

    pending_rows = db.execute(
        select(User)
        .join(
            SchoolAccessRequest,
            (SchoolAccessRequest.user_id == User.id)
            & (SchoolAccessRequest.school_id == school.id),
        )
        .outerjoin(
            UserSchoolAccess,
            (UserSchoolAccess.user_id == User.id)
            & (UserSchoolAccess.school_id == school.id),
        )
        .where(
            User.is_active.is_(True),
            SchoolAccessRequest.status == "pending",
            UserSchoolAccess.id.is_(None),
        )
    ).scalars().all()

    assignments = [
        SchoolUserAssignmentResponse(
            user_uuid=user.uuid,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            mobile=user.mobile,
            designation=user.designation,
            role=role,
            assignment_status="assigned",
        )
        for user, role in assigned_rows
    ]

    assignments.extend(
        SchoolUserAssignmentResponse(
            user_uuid=user.uuid,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            mobile=user.mobile,
            designation=user.designation,
            role=None,
            assignment_status="pending_assignment",
        )
        for user in pending_rows
    )

    return sorted(
        assignments,
        key=lambda item: (item.full_name.casefold(), item.username.casefold()),
    )

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

    access_request = db.execute(
        select(SchoolAccessRequest).where(
            SchoolAccessRequest.user_id == user.id,
            SchoolAccessRequest.school_id == school.id,
            SchoolAccessRequest.status == "pending",
        )
    ).scalar_one_or_none()

    if not is_platform_admin(current_user) and access_request is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user has not requested access to this school",
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
    if access_request is not None:
        access_request.status = "approved"
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


# Global account changes affect every school; only platform administrators may
# perform them. School administrators retain existing school-scoped role APIs.
@router.get("", response_model=list[UserResponse])
def list_accounts(offset: int = 0, limit: int = 100, search: str = "",
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    if offset < 0 or not 1 <= limit <= 200 or len(search) > 150:
        raise HTTPException(422, "Invalid directory pagination")
    query = select(User).order_by(User.full_name, User.id).offset(offset).limit(limit)
    if search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(or_(User.username.ilike(f"%{term}%", escape="\\"),
                               User.full_name.ilike(f"%{term}%", escape="\\")))
    return db.execute(query).scalars().all()


def _check_account_identity(db, username=None, email=None, exclude_id=None):
    conditions = []
    if username is not None:
        conditions.append(User.username == username)
    if email:
        local, separator, domain = email.rpartition("@")
        if not separator or not local or "." not in domain:
            raise HTTPException(422, "Enter a valid email address")
        conditions.append(func.lower(User.email) == email.lower())
    if conditions:
        query = select(User).where(or_(*conditions))
        if exclude_id is not None:
            query = query.where(User.id != exclude_id)
        if db.execute(query).scalars().first() is not None:
            raise HTTPException(409, "Username or email already exists")


@router.post("/accounts", response_model=UserResponse, status_code=201)
def create_account(data: AdminUserCreate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    _check_account_identity(db, data.username, data.email)
    values = data.model_dump(exclude={"password"})
    values["full_name"] = values["full_name"].strip()
    if not values["full_name"]:
        raise HTTPException(422, "Full name cannot be empty")
    user = User(**values, password_hash=hash_password(data.password), is_active=True,
                is_platform_admin=False, platform_role=None)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    return user


@router.patch("/{user_uuid}/account", response_model=UserResponse)
def update_account(user_uuid: UUID, data: AdminUserUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    # Lock critical administrators in a stable order before the target. This
    # serializes concurrent demotions/deactivations and protects the last admin.
    admins = db.execute(select(User).where(User.is_active.is_(True),
        or_(User.platform_role == "platform_admin", User.is_platform_admin.is_(True)))
        .order_by(User.id).with_for_update()).scalars().all()
    user = db.execute(select(User).where(User.uuid == user_uuid).with_for_update()).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")
    values = data.model_dump(exclude_unset=True)
    if current_user.id == user.id and (values.get("is_active") is False or "platform_role" in values):
        raise HTTPException(409, "You cannot deactivate yourself or change your own platform role")
    loses_admin = values.get("is_active") is False or ("platform_role" in values and values["platform_role"] is None)
    if user.is_active and is_platform_admin(user) and loses_admin and len(admins) <= 1:
        raise HTTPException(409, "The last active platform administrator must be retained")
    _check_account_identity(db, email=values.get("email"), exclude_id=user.id)
    if "password" in values:
        user.password_hash = hash_password(values.pop("password"))
    if "platform_role" in values:
        user.is_platform_admin = values["platform_role"] == "platform_admin"
    for name, value in values.items():
        setattr(user, name, value.strip() if isinstance(value, str) else value)
    if "is_active" in data.model_fields_set or "password" in data.model_fields_set:
        from app.core.auth_sessions import revoke_user_sessions
        revoke_user_sessions(db, user.id)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    return user
