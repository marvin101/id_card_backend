from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.academic_session import AcademicSession
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User
from app.schemas.academic_session import (
    AcademicSessionCreate,
    AcademicSessionResponse,
)
from uuid import UUID


router = APIRouter(
    prefix="/schools/{school_uuid}/academic-sessions",
    tags=["Academic Sessions"],
)


# ==========================================================
# Create Academic Session
# ==========================================================

@router.post(
    "",
    response_model=AcademicSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_academic_session(
    school_uuid: UUID,
    session_data: AcademicSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find the school
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
    # Check user's access to the school
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == current_user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this school",
        )

    # ------------------------------------------------------
    # Only school admin can create academic sessions
    # ------------------------------------------------------

    if access.role != "admin" and not current_user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a school administrator can create academic sessions",
        )

    # ------------------------------------------------------
    # Check duplicate session name
    # ------------------------------------------------------

    existing_session = db.execute(
        select(AcademicSession).where(
            AcademicSession.school_id == school.id,
            AcademicSession.name == session_data.name,
        )
    ).scalar_one_or_none()

    if existing_session is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Academic session already exists",
        )

    # ------------------------------------------------------
    # If this session is current, unset other current sessions
    # ------------------------------------------------------

    if session_data.is_current:
        current_sessions = db.execute(
            select(AcademicSession).where(
                AcademicSession.school_id == school.id,
                AcademicSession.is_current.is_(True),
            )
        ).scalars().all()

        for current_session in current_sessions:
            current_session.is_current = False

    # ------------------------------------------------------
    # Create session
    # ------------------------------------------------------

    academic_session = AcademicSession(
        school_id=school.id,
        **session_data.model_dump(),
    )

    db.add(academic_session)
    db.commit()
    db.refresh(academic_session)

    return academic_session


# ==========================================================
# List Academic Sessions
# ==========================================================

@router.get(
    "",
    response_model=list[AcademicSessionResponse],
)
def list_academic_sessions(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find the school
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
    # Check user's access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == current_user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this school",
        )

    # ------------------------------------------------------
    # Return sessions
    # ------------------------------------------------------

    result = db.execute(
        select(AcademicSession)
        .where(
            AcademicSession.school_id == school.id,
        )
        .order_by(AcademicSession.start_date)
    )

    return result.scalars().all()
# ==========================================================
# Get Academic Session
# ==========================================================

@router.get(
    "/{session_uuid}",
    response_model=AcademicSessionResponse,
)
def get_academic_session(
    school_uuid: UUID,
    session_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find the school
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
    # Check user's access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == current_user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this school",
        )

    # ------------------------------------------------------
    # Find academic session
    # ------------------------------------------------------

    academic_session = db.execute(
        select(AcademicSession).where(
            AcademicSession.uuid == session_uuid,
            AcademicSession.school_id == school.id,
        )
    ).scalar_one_or_none()

    if academic_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Academic session not found",
        )

    return academic_session