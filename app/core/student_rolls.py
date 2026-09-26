from sqlalchemy import Select, select

from app.models.student import Student


def student_roll_key(
    session_id: int,
    class_id: int,
    section_id: int,
    roll_no: str,
) -> tuple[int, int, int, str]:
    return session_id, class_id, section_id, roll_no


def student_roll_conflict_query(
    *,
    school_id: int,
    session_id: int,
    class_id: int,
    section_id: int,
    roll_no: str,
    exclude_student_id: int | None = None,
) -> Select[tuple[Student]]:
    query = select(Student).where(
        Student.school_id == school_id,
        Student.session_id == session_id,
        Student.class_id == class_id,
        Student.section_id == section_id,
        Student.roll_no == roll_no,
    )
    if exclude_student_id is not None:
        query = query.where(Student.id != exclude_student_id)
    return query
