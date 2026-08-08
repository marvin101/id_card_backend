from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User
from app.schemas.school import SchoolCreate, SchoolResponse


router = APIRouter(
    prefix="/schools",
    tags=["Schools"],
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

    access = UserSchoolAccess(
        user_id=current_user.id,
        school_id=school.id,
        role="admin",
    )

    db.add(access)

    db.commit()
    db.refresh(school)

    return school


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

    return result.scalars().all()