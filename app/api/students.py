from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.users import User
from app.schemas import school
from app.schemas.student import StudentCreate, StudentResponse, StudentUpdate


router = APIRouter(
    prefix="/schools/{school_uuid}/students",
    tags=["Students"],
)


# ==========================================================
# Create Student
# ==========================================================

@router.post(
    "",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_student(
    school_uuid: UUID,
    student_data: StudentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can create students')


    # ------------------------------------------------------
    # Find academic session
    # ------------------------------------------------------

    session = db.execute(
        select(AcademicSession).where(
            AcademicSession.uuid == student_data.session_uuid,
            AcademicSession.school_id == school.id,
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Academic session not found in this school",
        )

    # ------------------------------------------------------
    # Find class
    # ------------------------------------------------------

    school_class = db.execute(
        select(SchoolClass).where(
            SchoolClass.uuid == student_data.class_uuid,
            SchoolClass.school_id == school.id,
        )
    ).scalar_one_or_none()

    if school_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found in this school",
        )

    # ------------------------------------------------------
    # Find section
    # ------------------------------------------------------

    section = db.execute(
        select(Section).where(
            Section.uuid == student_data.section_uuid,
            Section.class_id == school_class.id,
        )
    ).scalar_one_or_none()

    if section is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Section not found in this class",
        )

    # ------------------------------------------------------
    # Check duplicate admission number
    # ------------------------------------------------------

    existing_student = db.execute(
        select(Student).where(
            Student.school_id == school.id,
            Student.admission_no == student_data.admission_no,
        )
    ).scalar_one_or_none()

    if existing_student is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Admission number already exists in this school",
        )

    # ------------------------------------------------------
    # Check duplicate roll number
    # ------------------------------------------------------

    if student_data.roll_no is not None:
        existing_roll = db.execute(
            select(Student).where(
                Student.school_id == school.id,
                Student.session_id == session.id,
                Student.class_id == school_class.id,
                Student.roll_no == student_data.roll_no,
            )
        ).scalar_one_or_none()

        if existing_roll is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Roll number already exists for this class in this academic session",
            )

    # ------------------------------------------------------
    # Create student
    # ------------------------------------------------------

    student = Student(
        school_id=school.id,
        session_id=session.id,
        class_id=school_class.id,
        section_id=section.id,
        admission_no=student_data.admission_no,
        roll_no=student_data.roll_no,
        stream=student_data.stream,
        full_name=student_data.full_name,
        father_name=student_data.father_name,
        mother_name=student_data.mother_name,
        dob=student_data.dob,
        gender=student_data.gender,
        blood_group=student_data.blood_group,
        mobile=student_data.mobile,
        aadhaar=student_data.aadhaar,
        address=student_data.address,
        photo_path=student_data.photo_path,
    )

    db.add(student)
    db.commit()
    db.refresh(student)

    return student


# ==========================================================
# List Students
# ==========================================================

@router.get(
    "",
    response_model=list[StudentResponse],
)
def list_students(
    school_uuid: UUID,
    admission_no: str | None = Query(
        default=None,
        description="Filter by admission number",
    ),
    session_uuid: UUID | None = Query(
        default=None,
        description="Filter by academic session",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can view students')


    # ------------------------------------------------------
    # Build student query
    # ------------------------------------------------------

    query = (
        select(Student)
        .where(
            Student.school_id == school.id,
            Student.is_active.is_(True),
        )
    )

    # ------------------------------------------------------
    # Filter by admission number
    # ------------------------------------------------------

    if admission_no:
        query = query.where(
            Student.admission_no == admission_no
        )

    # ------------------------------------------------------
    # Filter by academic session
    # ------------------------------------------------------

    if session_uuid:
        session = db.execute(
            select(AcademicSession).where(
                AcademicSession.uuid == session_uuid,
                AcademicSession.school_id == school.id,
            )
        ).scalar_one_or_none()

        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic session not found",
            )

        query = query.where(
            Student.session_id == session.id
        )

    # ------------------------------------------------------
    # Order students
    # ------------------------------------------------------

    query = query.order_by(Student.full_name)

    result = db.execute(query)

    return result.scalars().all()


# ==========================================================
# Get Student
# ==========================================================

@router.get(
    "/{student_uuid}",
    response_model=StudentResponse,
)
def get_student(
    school_uuid: UUID,
    student_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school access
    # ------------------------------------------------------

    require_school_access(db, current_user, school.id)

    # ------------------------------------------------------
    # Find student
    # ------------------------------------------------------

    student = db.execute(
        select(Student).where(
            Student.uuid == student_uuid,
            Student.school_id == school.id,
            Student.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found",
        )

    return student


# ==========================================================
# Update Student
# ==========================================================

@router.put(
    "/{student_uuid}",
    response_model=StudentResponse,
)
def update_student(
    school_uuid: UUID,
    student_uuid: UUID,
    student_data: StudentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can update students')


    # ------------------------------------------------------
    # Find student
    # ------------------------------------------------------

    student = db.execute(
        select(Student).where(
            Student.uuid == student_uuid,
            Student.school_id == school.id,
        )
    ).scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found",
        )

    # ------------------------------------------------------
    # Update academic session
    # ------------------------------------------------------

    if student_data.session_uuid is not None:
        session = db.execute(
            select(AcademicSession).where(
                AcademicSession.uuid == student_data.session_uuid,
                AcademicSession.school_id == school.id,
            )
        ).scalar_one_or_none()

        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic session not found in this school",
            )

        student.session_id = session.id

    # ------------------------------------------------------
    # Update class
    # ------------------------------------------------------

    if student_data.class_uuid is not None:
        school_class = db.execute(
            select(SchoolClass).where(
                SchoolClass.uuid == student_data.class_uuid,
                SchoolClass.school_id == school.id,
            )
        ).scalar_one_or_none()

        if school_class is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Class not found in this school",
            )

        student.class_id = school_class.id

    # ------------------------------------------------------
    # Update section
    # ------------------------------------------------------

    if student_data.section_uuid is not None:
        section = db.execute(
            select(Section).where(
                Section.uuid == student_data.section_uuid,
                Section.class_id == student.class_id,
            )
        ).scalar_one_or_none()

        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Section not found in this class",
            )

        student.section_id = section.id

    # ------------------------------------------------------
    # ------------------------------------------------------
    # Update admission number
    # ------------------------------------------------------

    if student_data.admission_no is not None:
        existing_student = db.execute(
            select(Student).where(
                Student.school_id == school.id,
                Student.admission_no == student_data.admission_no,
                Student.id != student.id,
            )
        ).scalar_one_or_none()

        if existing_student is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Admission number already exists in this school",
            )

        student.admission_no = student_data.admission_no
    # ------------------------------------------------------
    # Validate roll number
    # ------------------------------------------------------

    if student_data.roll_no is not None:
        existing_roll = db.execute(
            select(Student).where(
                Student.school_id == school.id,
                Student.session_id == student.session_id,
                Student.class_id == student.class_id,
                Student.roll_no == student_data.roll_no,
                Student.id != student.id,
            )
        ).scalar_one_or_none()

        if existing_roll is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Roll number already exists for this class in this academic session",
            )

    # ------------------------------------------------------
    # Update remaining fields
    # ------------------------------------------------------

    if student_data.roll_no is not None:
        student.roll_no = student_data.roll_no

    if student_data.stream is not None:
        student.stream = student_data.stream

    if student_data.full_name is not None:
        student.full_name = student_data.full_name

    if student_data.father_name is not None:
        student.father_name = student_data.father_name

    if student_data.mother_name is not None:
        student.mother_name = student_data.mother_name

    if student_data.dob is not None:
        student.dob = student_data.dob

    if student_data.gender is not None:
        student.gender = student_data.gender

    if student_data.blood_group is not None:
        student.blood_group = student_data.blood_group

    if student_data.mobile is not None:
        student.mobile = student_data.mobile

    if student_data.aadhaar is not None:
        student.aadhaar = student_data.aadhaar

    if student_data.address is not None:
        student.address = student_data.address

    if student_data.photo_path is not None:
        student.photo_path = student_data.photo_path

    if student_data.is_active is not None:
        student.is_active = student_data.is_active

    # ------------------------------------------------------
    # Save changes
    # ------------------------------------------------------

    db.commit()
    db.refresh(student)

    return student

# ==========================================================
# Delete Student
# ==========================================================

@router.delete(
    "/{student_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_student(
    school_uuid: UUID,
    student_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(db, current_user, school.id, 'Only a school administrator can delete students')


    # ------------------------------------------------------
    # Find student
    # ------------------------------------------------------

    student = db.execute(
        select(Student).where(
            Student.uuid == student_uuid,
            Student.school_id == school.id,
            Student.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found",
        )

    # ------------------------------------------------------
    # Soft delete
    # ------------------------------------------------------

    student.is_active = False

    db.commit()

    return None