from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.school_access import (
    get_active_school,
    require_school_access,
    require_school_admin,
)
from app.models.academic_session import AcademicSession
from app.models.users import User
from app.schemas.academic_session import (
    AcademicSessionCreate,
    AcademicSessionResponse,
    AcademicSessionUpdate,
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
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can create academic sessions')


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
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school access
    # ------------------------------------------------------

    require_school_access(db, current_user, school.id)

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
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school access
    # ------------------------------------------------------

    require_school_access(db, current_user, school.id)

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
# ==========================================================
# Update Academic Session
# ==========================================================

@router.put(
    "/{session_uuid}",
    response_model=AcademicSessionResponse,
)
def update_academic_session(
    school_uuid: UUID,
    session_uuid: UUID,
    session_data: AcademicSessionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can update academic sessions')


    # ------------------------------------------------------
    # Find the session and verify it belongs to this school
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
            detail="Academic session not found in this school",
        )

    # ------------------------------------------------------
    # Check duplicate session name (excluding this session)
    # ------------------------------------------------------

    if session_data.name is not None:
        existing_session = db.execute(
            select(AcademicSession).where(
                AcademicSession.school_id == school.id,
                AcademicSession.name == session_data.name,
                AcademicSession.id != academic_session.id,
            )
        ).scalar_one_or_none()

        if existing_session is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Academic session already exists",
            )

        academic_session.name = session_data.name

    # ------------------------------------------------------
    # Validate and apply dates
    # ------------------------------------------------------

    # ``None`` is meaningful here: it clears an existing date.
    # ``model_fields_set`` lets us distinguish an omitted field from an
    # explicitly supplied null value.
    start_date_supplied = 'start_date' in session_data.model_fields_set
    end_date_supplied = 'end_date' in session_data.model_fields_set

    new_start_date = (
        session_data.start_date
        if start_date_supplied
        else academic_session.start_date
    )

    new_end_date = (
        session_data.end_date
        if end_date_supplied
        else academic_session.end_date
    )

    if (
        new_start_date is not None
        and new_end_date is not None
        and new_end_date < new_start_date
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date cannot be earlier than start_date",
        )

    if start_date_supplied:
        academic_session.start_date = session_data.start_date

    if end_date_supplied:
        academic_session.end_date = session_data.end_date

    # ------------------------------------------------------
    # Handle is_current toggle
    # ------------------------------------------------------

    if session_data.is_current is not None:
        if session_data.is_current:
            other_current_sessions = db.execute(
                select(AcademicSession).where(
                    AcademicSession.school_id == school.id,
                    AcademicSession.is_current.is_(True),
                    AcademicSession.id != academic_session.id,
                )
            ).scalars().all()

            for other_session in other_current_sessions:
                other_session.is_current = False

        academic_session.is_current = session_data.is_current

    # ------------------------------------------------------
    # Save changes
    # ------------------------------------------------------

    db.commit()
    db.refresh(academic_session)

    return academic_session