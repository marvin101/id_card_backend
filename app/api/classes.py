from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.student import Student
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.school_access import (
    get_active_school,
    require_school_access,
    require_school_admin,
)
from app.models.school_class import SchoolClass
from app.models.users import User
from app.schemas.school_class import (
    SchoolClassCreate,
    SchoolClassResponse,
    SchoolClassUpdate,
)


router = APIRouter(
    prefix="/schools/{school_uuid}/classes",
    tags=["Classes"],
)


# ==========================================================
# Create Class
# ==========================================================

@router.post(
    "",
    response_model=SchoolClassResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_class(
    school_uuid: UUID,
    class_data: SchoolClassCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can create classes')


    # ------------------------------------------------------
    # Check duplicate class name
    # ------------------------------------------------------

    existing_class = db.execute(
        select(SchoolClass).where(
            SchoolClass.school_id == school.id,
            SchoolClass.name == class_data.name,
        )
    ).scalar_one_or_none()

    if existing_class is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Class already exists",
        )

    # ------------------------------------------------------
    # Create class
    # ------------------------------------------------------

    school_class = SchoolClass(
        school_id=school.id,
        name=class_data.name,
    )

    db.add(school_class)
    db.commit()
    db.refresh(school_class)

    return school_class


# ==========================================================
# List Classes
# ==========================================================

@router.get(
    "",
    response_model=list[SchoolClassResponse],
)
def list_classes(
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
    # Get all classes
    # ------------------------------------------------------

    result = db.execute(
        select(SchoolClass)
        .where(SchoolClass.school_id == school.id)
        .order_by(SchoolClass.name)
    )

    return result.scalars().all()


# ==========================================================
# Get Class
# ==========================================================

@router.get(
    "/{class_uuid}",
    response_model=SchoolClassResponse,
)
def get_class(
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
    # Find class
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
            detail="Class not found",
        )

    return school_class


# ==========================================================
# Update Class
# ==========================================================

@router.put(
    "/{class_uuid}",
    response_model=SchoolClassResponse,
)
def update_class(
    school_uuid: UUID,
    class_uuid: UUID,
    class_data: SchoolClassUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can update classes')


    # ------------------------------------------------------
    # Find class
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
            detail="Class not found",
        )

    # ------------------------------------------------------
    # Check duplicate class name
    # ------------------------------------------------------

    if class_data.name is not None:
        existing_class = db.execute(
            select(SchoolClass).where(
                SchoolClass.school_id == school.id,
                SchoolClass.name == class_data.name,
                SchoolClass.id != school_class.id,
            )
        ).scalar_one_or_none()

        if existing_class is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Class already exists",
            )

        school_class.name = class_data.name

    # ------------------------------------------------------
    # Save changes
    # ------------------------------------------------------

    db.commit()
    db.refresh(school_class)

    return school_class


# ==========================================================
# Delete Class
# ==========================================================

@router.delete(
    "/{class_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_class(
    school_uuid: UUID,
    class_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can delete classes')


    # ------------------------------------------------------
    # Find class
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
            detail="Class not found",
        )
        # ------------------------------------------------------
    # Check whether students are assigned to this class
    # ------------------------------------------------------

    students_exist = db.execute(
        select(Student.id)
        .where(
            Student.class_id == school_class.id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if students_exist is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete class because students are assigned to it",
        )
    
    # ------------------------------------------------------
    # Delete class
    # ------------------------------------------------------

    db.delete(school_class)
    db.commit()

    return None
