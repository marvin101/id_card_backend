from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.school import School
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User
from app.schemas.section import SectionCreate, SectionResponse


router = APIRouter(
    prefix="/schools/{school_uuid}/classes/{class_uuid}/sections",
    tags=["Sections"],
)


# ==========================================================
# Create Section
# ==========================================================

@router.post(
    "",
    response_model=SectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_section(
    school_uuid: UUID,
    class_uuid: UUID,
    section_data: SectionCreate,
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
    # Only school admin can create sections
    # ------------------------------------------------------

    if access.role != "admin" and not current_user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a school administrator can create sections",
        )

    # ------------------------------------------------------
    # Find the class
    # ------------------------------------------------------

    school_class = db.execute(
        select(SchoolClass).where(
            SchoolClass.uuid == class_uuid,
            SchoolClass.school_id == school.id,
        )
    ).scalar_one_or_none()

    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found in this school",
        )

    # ------------------------------------------------------
    # Check duplicate section name
    # ------------------------------------------------------

    existing_section = db.execute(
        select(Section).where(
            Section.class_id == school_class.id,
            Section.name == section_data.name,
        )
    ).scalar_one_or_none()

    if existing_section is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Section already exists in this class",
        )

    # ------------------------------------------------------
    # Create section
    # ------------------------------------------------------

    section = Section(
        class_id=school_class.id,
        name=section_data.name,
    )

    db.add(section)
    db.commit()
    db.refresh(section)

    return section


# ==========================================================
# List Sections
# ==========================================================

@router.get(
    "",
    response_model=list[SectionResponse],
)
def list_sections(
    school_uuid: UUID,
    class_uuid: UUID,
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
    # Find the class
    # ------------------------------------------------------

    school_class = db.execute(
        select(SchoolClass).where(
            SchoolClass.uuid == class_uuid,
            SchoolClass.school_id == school.id,
        )
    ).scalar_one_or_none()

    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found in this school",
        )

    # ------------------------------------------------------
    # Return sections
    # ------------------------------------------------------

    result = db.execute(
        select(Section)
        .where(
            Section.class_id == school_class.id,
        )
        .order_by(Section.name)
    )

    return result.scalars().all()