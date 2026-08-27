from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User


PLATFORM_ADMIN_ROLE = "platform_admin"
SCHOOL_ADMIN_ROLE = "school_admin"
CARD_OPERATOR_ROLE = "card_operator"
TEACHER_ROLE = "teacher"
STAFF_ROLE = "staff"
ORDINARY_SCHOOL_ROLES = frozenset({TEACHER_ROLE, STAFF_ROLE})
LEGACY_SCHOOL_ADMIN_ROLE = "admin"


def is_platform_admin(user: User) -> bool:
    """Return whether a user has platform-wide administrative authority.

    ``is_platform_admin`` remains a compatibility fallback while existing
    administrator records are migrated to ``platform_role``.
    """
    return user.platform_role == PLATFORM_ADMIN_ROLE or user.is_platform_admin


def require_platform_admin(current_user: User) -> None:
    """Require platform-wide administrative authority."""
    if not is_platform_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator can perform this action",
        )


def get_active_school(db: Session, school_uuid: UUID) -> School:
    """Return an active school or raise 404."""
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

    return school


def require_school_access(
    db: Session,
    current_user: User,
    school_id: int,
) -> UserSchoolAccess | None:
    """Require access to the school; platform admins bypass membership."""
    if is_platform_admin(current_user):
        return None

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == current_user.id,
            UserSchoolAccess.school_id == school_id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this school",
        )

    return access


def require_school_role(
    db: Session,
    current_user: User,
    school_id: int,
    role: str,
    detail: str,
    *,
    allow_legacy_school_admin: bool = False,
) -> UserSchoolAccess | None:
    """Require a specific role within a specific school.

    Platform administrators bypass school membership. All other users must
    have a UserSchoolAccess record for the requested school and that record
    must contain the requested role. Legacy ``admin`` is accepted only when
    explicitly enabled for the school-admin compatibility path.
    """
    if is_platform_admin(current_user):
        return None

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == current_user.id,
            UserSchoolAccess.school_id == school_id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this school",
        )

    allowed_roles = {role}
    if allow_legacy_school_admin and role == SCHOOL_ADMIN_ROLE:
        allowed_roles.add(LEGACY_SCHOOL_ADMIN_ROLE)

    if access.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )

    return access


def require_school_admin(
    db: Session,
    current_user: User,
    school_id: int,
    detail: str,
) -> UserSchoolAccess | None:
    """Require school-admin access, preserving legacy ``admin`` records."""
    return require_school_role(
        db,
        current_user,
        school_id,
        SCHOOL_ADMIN_ROLE,
        detail,
        allow_legacy_school_admin=True,
    )


def require_card_data_access(
    db: Session,
    current_user: User,
    school_id: int,
    detail: str = "Only a school administrator or card operator can access student card data",
) -> UserSchoolAccess | None:
    """Allow card-data work only within the user's assigned school.

    Platform administrators bypass school membership. School administrators
    (including legacy ``admin`` rows) and card operators may view, create,
    update, and photograph student card records. This permission deliberately
    excludes deletion and all school-configuration operations.
    """
    if is_platform_admin(current_user):
        return None

    access = require_school_access(db, current_user, school_id)
    if access.role not in {
        SCHOOL_ADMIN_ROLE,
        LEGACY_SCHOOL_ADMIN_ROLE,
        CARD_OPERATOR_ROLE,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )

    return access


def require_school_role_management(
    db: Session,
    current_user: User,
    school_id: int,
    detail: str,
    *,
    existing_role: str | None = None,
    requested_role: str | None = None,
) -> UserSchoolAccess | None:
    """Require permission to manage a user's role in a specific school.

    Platform administrators may manage any valid school role. School
    administrators may manage only ordinary roles (teacher/staff), and only
    within schools where they have school-admin access.

    ``existing_role`` and ``requested_role`` are checked for School Admins so
    they cannot modify or revoke an elevated school role.
    """
    if is_platform_admin(current_user):
        return None

    require_school_admin(db, current_user, school_id, detail)

    if existing_role is not None and existing_role not in ORDINARY_SCHOOL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator can manage elevated school roles",
        )

    if requested_role is not None and requested_role not in ORDINARY_SCHOOL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a platform administrator can assign this school role",
        )

    return None


def require_card_operator(
    db: Session,
    current_user: User,
    school_id: int,
    detail: str = "Only a card operator assigned to this school can perform this action",
) -> UserSchoolAccess | None:
    """Require card-operator access to the specific requested school."""
    return require_school_role(
        db,
        current_user,
        school_id,
        CARD_OPERATOR_ROLE,
        detail,
    )
