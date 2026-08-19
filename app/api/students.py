from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.file_storage import save_student_photo
from app.core.school_access import (
    get_active_school,
    require_school_access,
    require_school_admin,
)
from app.core.security import get_current_user
from app.models.academic_session import AcademicSession
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.users import User
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
async def create_student(
    school_uuid: UUID,
    student_data_json: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        student_data = StudentCreate.model_validate_json(student_data_json)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid student data.",
        ) from exc

    school = get_active_school(db, school_uuid)

    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can create students",
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
        photo_path=None,
    )

    db.add(student)
    db.commit()
    db.refresh(student)

    return student

# ==========================================================
# Upload / Replace Student Photo
# ==========================================================

@router.post(
    "/{student_uuid}/photo",
    response_model=StudentResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_student_photo(
    school_uuid: UUID,
    student_uuid: UUID,
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)

    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can upload student photos",
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

    # ------------------------------------------------------
    # Read uploaded photo
    # ------------------------------------------------------

    content = await photo.read()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded photo is empty.",
        )

    # ------------------------------------------------------
    # Save photo
    # ------------------------------------------------------

    try:
        saved_photo_path = save_student_photo(
            student_uuid=student.uuid,
            content=content,
            content_type=photo.content_type,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # ------------------------------------------------------
    # Update student photo path
    # ------------------------------------------------------

    student.photo_path = saved_photo_path

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
    class_uuid: UUID | None = Query(
        default=None,
        description="Filter by class",
    ),
    section_uuid: UUID | None = Query(
        default=None,
        description="Filter by section",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)

    # ------------------------------------------------------
    # Check school administrator access
    # ------------------------------------------------------

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can view students",
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
    # Filter by class
    # ------------------------------------------------------

    if class_uuid:
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

        query = query.where(
            Student.class_id == school_class.id
        )

    # ------------------------------------------------------
    # Filter by section
    # ------------------------------------------------------

    if section_uuid:
        section = db.execute(
            select(Section)
            .join(
                SchoolClass,
                SchoolClass.id == Section.class_id,
            )
            .where(
                Section.uuid == section_uuid,
                SchoolClass.school_id == school.id,
            )
        ).scalar_one_or_none()

        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Section not found",
            )

        query = query.where(
            Student.section_id == section.id
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

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can update students",
    )

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

    fields_set = student_data.model_fields_set

    # ------------------------------------------------------
    # Determine target academic placement
    # ------------------------------------------------------

    target_session_id = student.session_id
    target_class_id = student.class_id
    target_section_id = student.section_id

    # ------------------------------------------------------
    # Validate session
    # ------------------------------------------------------

    if "session_uuid" in fields_set:
        if student_data.session_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="session_uuid cannot be null",
            )

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

        target_session_id = session.id

    # ------------------------------------------------------
    # Validate class
    # ------------------------------------------------------

    if "class_uuid" in fields_set:
        if student_data.class_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="class_uuid cannot be null",
            )

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

        target_class_id = school_class.id

    # ------------------------------------------------------
    # Validate section
    # ------------------------------------------------------

    if "section_uuid" in fields_set:
        if student_data.section_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="section_uuid cannot be null",
            )

        section = db.execute(
            select(Section).where(
                Section.uuid == student_data.section_uuid,
                Section.class_id == target_class_id,
            )
        ).scalar_one_or_none()

        if section is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Section not found in the selected class",
            )

        target_section_id = section.id

    # ------------------------------------------------------
    # Apply academic placement
    # ------------------------------------------------------

    student.session_id = target_session_id
    student.class_id = target_class_id
    student.section_id = target_section_id

    # ------------------------------------------------------
    # Validate roll number in target placement
    # ------------------------------------------------------

    target_roll_no = (
        student_data.roll_no
        if "roll_no" in fields_set
        else student.roll_no
    )

    if target_roll_no is not None:
        existing_roll = db.execute(
            select(Student).where(
                Student.school_id == school.id,
                Student.session_id == target_session_id,
                Student.class_id == target_class_id,
                Student.roll_no == target_roll_no,
                Student.id != student.id,
            )
        ).scalar_one_or_none()

        if existing_roll is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Roll number already exists for this class "
                    "in this academic session"
                ),
            )

    # ------------------------------------------------------
    # Update admission number
    # ------------------------------------------------------

    if "admission_no" in fields_set:
        if student_data.admission_no is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="admission_no cannot be null",
            )

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
    # Update remaining fields
    # ------------------------------------------------------

    if "roll_no" in fields_set:
        student.roll_no = student_data.roll_no

    if "stream" in fields_set:
        student.stream = student_data.stream

    if "full_name" in fields_set:
        if student_data.full_name is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="full_name cannot be null",
            )

        student.full_name = student_data.full_name

    if "father_name" in fields_set:
        student.father_name = student_data.father_name

    if "mother_name" in fields_set:
        student.mother_name = student_data.mother_name

    if "dob" in fields_set:
        student.dob = student_data.dob

    if "gender" in fields_set:
        student.gender = student_data.gender

    if "blood_group" in fields_set:
        student.blood_group = student_data.blood_group

    if "mobile" in fields_set:
        student.mobile = student_data.mobile

    if "aadhaar" in fields_set:
        student.aadhaar = student_data.aadhaar

    if "address" in fields_set:
        student.address = student_data.address

    if "photo_path" in fields_set:
        student.photo_path = student_data.photo_path

    if "is_active" in fields_set:
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

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can delete students",
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

    # ------------------------------------------------------
    # Soft delete
    # ------------------------------------------------------

    student.is_active = False

    db.commit()

    return None