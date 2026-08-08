from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.academic_session import AcademicSession
from app.models.school import School
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.user_school_access import UserSchoolAccess
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
    # Check permission
    # ------------------------------------------------------

    if access.role != "admin" and not current_user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a school administrator can create students",
        )

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
            Student.session_id == session.id,
            Student.admission_no == student_data.admission_no,
        )
    ).scalar_one_or_none()

    if existing_student is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Admission number already exists for this academic session",
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