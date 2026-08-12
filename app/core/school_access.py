from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User


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
    """Require access to the school; platform admins bypass school membership."""
    if current_user.is_platform_admin:
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


def require_school_admin(
    db: Session,
    current_user: User,
    school_id: int,
    detail: str,
) -> UserSchoolAccess | None:
    """Require school-admin access; platform admins bypass school membership."""
    if current_user.is_platform_admin:
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

    if access.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )

    return access
