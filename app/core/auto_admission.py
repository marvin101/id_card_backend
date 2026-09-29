from typing import Any
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.student_audit import record_student_audit
from app.models.school import School
from app.models.student import Student
from app.models.users import User


DEFAULT_STREAM_OPTIONS = [
    {"name": "Science", "code": "SCI"},
    {"name": "Arts", "code": "ARTS"},
    {"name": "Commerce", "code": "COM"},
]


def resolve_stream_code(stream: str | None, stream_options: list[dict[str, Any]] | None = None) -> str | None:
    if not stream or not stream.strip():
        return None
    raw = stream.strip()
    raw_lower = raw.casefold()

    options = stream_options if stream_options is not None else DEFAULT_STREAM_OPTIONS
    for opt in options:
        name = str(opt.get("name", "")).strip()
        code = str(opt.get("code", "")).strip()
        if raw_lower == name.casefold() or raw_lower == code.casefold():
            return code.upper()

    # Fallback to standard defaults if not in custom list
    for opt in DEFAULT_STREAM_OPTIONS:
        if raw_lower == opt["name"].casefold() or raw_lower == opt["code"].casefold():
            return opt["code"].upper()

    # Generic fallback: uppercase first 3-4 alphanumeric characters
    cleaned = "".join(ch for ch in raw if ch.isalnum()).upper()
    return cleaned[:4] if cleaned else None


def format_admission_no(
    stream: str | None,
    roll_no: str | None,
    stream_options: list[dict[str, Any]] | None = None,
) -> str | None:
    if not stream or not roll_no:
        return None
    code = resolve_stream_code(stream, stream_options)
    roll = roll_no.strip()
    if not code or not roll:
        return None
    return f"{code}/{roll}"


def apply_auto_admission_to_existing_students(
    db: Session,
    school: School,
    actor: User | None = None,
) -> int:
    """Modify the admission fields of all active students in the school to the automated format.

    Validates that no duplicate admission numbers are generated before applying changes.
    """
    students = (
        db.execute(
            select(Student)
            .where(
                Student.school_id == school.id,
                Student.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )

    planned_updates: list[tuple[Student, str, str]] = []  # (student, old_adm, new_adm)
    all_final_admissions: dict[str, list[Student]] = {}

    for student in students:
        old_adm = student.admission_no
        new_adm = format_admission_no(student.stream, student.roll_no, school.stream_options)

        final_adm = new_adm if new_adm else old_adm
        all_final_admissions.setdefault(final_adm, []).append(student)

        if new_adm and new_adm != old_adm:
            planned_updates.append((student, old_adm, new_adm))

    # Detect duplicate admission collisions
    collisions = {
        adm: student_list
        for adm, student_list in all_final_admissions.items()
        if len(student_list) > 1 and any(s in [p[0] for p in planned_updates] for s in student_list)
    }

    if collisions:
        summary_items = []
        for adm, cl_students in list(collisions.items())[:5]:
            names = ", ".join(f"'{s.full_name}' (Roll: {s.roll_no}, Stream: {s.stream})" for s in cl_students)
            summary_items.append(f"{adm}: {names}")
        details = "; ".join(summary_items)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot enable automated admission format because duplicate admission numbers would be produced: {details}. "
                "Please resolve duplicate stream and roll numbers before enabling."
            ),
        )

    updated_count = 0
    for student, old_adm, new_adm in planned_updates:
        student.admission_no = new_adm
        updated_count += 1
        record_student_audit(
            db,
            student=student,
            actor=actor,
            event_type="update",
            field_name="admission_no",
            old_value=old_adm,
            new_value=new_adm,
            note="Automated admission number format enabled",
        )

    return updated_count
