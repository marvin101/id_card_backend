from uuid import UUID

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
from app.models.student import Student
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.users import User
from app.schemas.section import ( 
    SectionCreate, 
    SectionResponse, 
    SectionUpdate,
)


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
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can create sections')


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
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school access
    # ------------------------------------------------------

    require_school_access(db, current_user, school.id)

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
# ==========================================================
# Get Section
# ==========================================================

@router.get(
    "/{section_uuid}",
    response_model=SectionResponse,
)
def get_section(
    school_uuid: UUID,
    class_uuid: UUID,
    section_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school access
    # ------------------------------------------------------

    require_school_access(db, current_user, school.id)

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
    # Find the section
    # ------------------------------------------------------

    section = db.execute(
        select(Section).where(
            Section.uuid == section_uuid,
            Section.class_id == school_class.id,
        )
    ).scalar_one_or_none()

    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found in this class",
        )

    return section
# ==========================================================
# Update Section
# ==========================================================

@router.put(
    "/{section_uuid}",
    response_model=SectionResponse,
)
def update_section(
    school_uuid: UUID,
    class_uuid: UUID,
    section_uuid: UUID,
    section_data: SectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can update sections')

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
    # Find the section
    # ------------------------------------------------------

    section = db.execute(
        select(Section).where(
            Section.uuid == section_uuid,
            Section.class_id == school_class.id,
        )
    ).scalar_one_or_none()

    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found in this class",
        )

    # ------------------------------------------------------
    # Check duplicate section name
    # ------------------------------------------------------

    if section_data.name is not None:
        existing_section = db.execute(
            select(Section).where(
                Section.class_id == school_class.id,
                Section.name == section_data.name,
                Section.id != section.id,
            )
        ).scalar_one_or_none()

        if existing_section is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Section already exists in this class",
            )

        section.name = section_data.name

    # ------------------------------------------------------
    # Save changes
    # ------------------------------------------------------

    db.commit()
    db.refresh(section)

    return section
# ==========================================================
# Delete Section
# ==========================================================

@router.delete(
    "/{section_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_section(
    school_uuid: UUID,
    class_uuid: UUID,
    section_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find the school
    # ------------------------------------------------------

    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can delete sections')


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
    # Find the section
    # ------------------------------------------------------

    section = db.execute(
        select(Section).where(
            Section.uuid == section_uuid,
            Section.class_id == school_class.id,
        )
    ).scalar_one_or_none()

    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found in this class",
        )

    # ------------------------------------------------------
    # Check whether students are assigned to this section
    # ------------------------------------------------------

    students_exist = db.execute(
        select(Student.id)
        .where(
            Student.section_id == section.id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if students_exist is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete section because students are assigned to it",
        )

    # ------------------------------------------------------
    # Delete section
    # ------------------------------------------------------

    db.delete(section)
    db.commit()

    return None