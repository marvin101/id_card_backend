from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.custom_fields import validate_student_custom_fields
from app.core.database import get_db
from app.core.school_access import get_active_school, require_card_data_access
from app.core.security import get_current_user
from app.core.student_audit import custom_field_change_set, record_student_field_changes
from app.models.academic_session import AcademicSession
from app.models.custom_field import CustomFieldDefinition, StudentCustomFieldValue
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.users import User
from app.schemas.student import BloodGroup, StudentCustomFieldInput
from app.schemas.student_grid import (
    StudentGridLookupItem,
    StudentGridPatchRequest,
    StudentGridPatchResponse,
    StudentGridResponse,
    StudentGridRow,
)


router = APIRouter(prefix="/schools/{school_uuid}/students/grid", tags=["Student Grid"])

EDITABLE_SYSTEM_FIELDS = frozenset(
    {
        "session_uuid",
        "class_uuid",
        "section_uuid",
        "admission_no",
        "roll_no",
        "stream",
        "full_name",
        "father_name",
        "mother_name",
        "dob",
        "gender",
        "blood_group",
        "mobile",
        "aadhaar",
        "address",
    }
)
REQUIRED_SYSTEM_FIELDS = frozenset(
    {"session_uuid", "class_uuid", "section_uuid", "admission_no", "full_name"}
)
TEXT_LIMITS = {
    "admission_no": 50,
    "roll_no": 30,
    "stream": 50,
    "full_name": 150,
    "father_name": 150,
    "mother_name": 150,
    "gender": 20,
    "mobile": 20,
    "aadhaar": 20,
}


def _row(student: Student) -> StudentGridRow:
    return StudentGridRow(
        uuid=student.uuid,
        updated_at=student.updated_at,
        session_uuid=student.session_uuid,
        class_uuid=student.class_uuid,
        section_uuid=student.section_uuid,
        admission_no=student.admission_no,
        roll_no=student.roll_no,
        stream=student.stream,
        full_name=student.full_name,
        father_name=student.father_name,
        mother_name=student.mother_name,
        dob=student.dob,
        gender=student.gender,
        blood_group=student.blood_group,
        mobile=student.mobile,
        aadhaar=student.aadhaar,
        address=student.address,
        custom_fields={
            str(value.field_definition.uuid): value.value
            for value in student.custom_field_values
            if value.field_definition.is_active
        },
    )


def _student_options():
    return (
        selectinload(Student.academic_session),
        selectinload(Student.school_class),
        selectinload(Student.section),
        selectinload(Student.custom_field_values).selectinload(
            StudentCustomFieldValue.field_definition
        ),
    )


def _error(student_uuid: UUID, field: str, message: str) -> dict[str, str]:
    return {"student_uuid": str(student_uuid), "field": field, "message": message}


def _error_response(errors: list[dict[str, str]], *, conflict: bool = False):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": "Grid conflict detected" if conflict else "Grid validation failed",
            "errors": errors,
        },
    )


def _clean_text(field: str, value: Any, student_uuid: UUID, errors: list[dict[str, str]]):
    if value is not None and not isinstance(value, str):
        errors.append(_error(student_uuid, field, "Must be text"))
        return None
    cleaned = value.strip() if isinstance(value, str) else None
    if not cleaned:
        if field in REQUIRED_SYSTEM_FIELDS:
            errors.append(_error(student_uuid, field, "This field is required"))
        return None
    limit = TEXT_LIMITS.get(field)
    if limit is not None and len(cleaned) > limit:
        errors.append(_error(student_uuid, field, f"Must be {limit} characters or fewer"))
    return cleaned


def _parse_uuid(field: str, value: Any, student_uuid: UUID, errors: list[dict[str, str]]):
    if value is None or value == "":
        errors.append(_error(student_uuid, field, "This field is required"))
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        errors.append(_error(student_uuid, field, "Must be a valid UUID"))
        return None


def _parse_date(value: Any, student_uuid: UUID, errors: list[dict[str, str]]):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(_error(student_uuid, "dob", "Must be a valid YYYY-MM-DD date"))
        return None


def _same_instant(left: datetime, right: datetime) -> bool:
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    return aware(left).astimezone(timezone.utc) == aware(right).astimezone(timezone.utc)


@router.get("", response_model=StudentGridResponse)
def get_student_grid(
    school_uuid: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    session_uuid: UUID | None = None,
    class_uuid: UUID | None = None,
    section_uuid: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_card_data_access(
        db, current_user, school.id,
        "Only a school administrator or card operator can use the student grid",
    )

    sessions = db.execute(
        select(AcademicSession).where(AcademicSession.school_id == school.id).order_by(AcademicSession.name)
    ).scalars().all()
    classes = db.execute(
        select(SchoolClass).where(SchoolClass.school_id == school.id).order_by(SchoolClass.name)
    ).scalars().all()
    sections = db.execute(
        select(Section).join(SchoolClass).where(SchoolClass.school_id == school.id).order_by(SchoolClass.name, Section.name)
    ).scalars().all()
    custom_fields = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.school_id == school.id,
            CustomFieldDefinition.entity_type == "student",
            CustomFieldDefinition.is_active.is_(True),
        ).order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)
    ).scalars().all()

    session_by_uuid = {item.uuid: item for item in sessions}
    class_by_uuid = {item.uuid: item for item in classes}
    section_by_uuid = {item.uuid: item for item in sections}
    if session_uuid is not None and session_uuid not in session_by_uuid:
        raise HTTPException(status_code=404, detail="Academic session not found")
    if class_uuid is not None and class_uuid not in class_by_uuid:
        raise HTTPException(status_code=404, detail="Class not found")
    if section_uuid is not None and section_uuid not in section_by_uuid:
        raise HTTPException(status_code=404, detail="Section not found")
    if section_uuid is not None and class_uuid is not None:
        if section_by_uuid[section_uuid].class_id != class_by_uuid[class_uuid].id:
            raise HTTPException(status_code=422, detail="Section does not belong to selected class")

    conditions = [Student.school_id == school.id, Student.is_active.is_(True)]
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        conditions.append(or_(Student.full_name.ilike(pattern), Student.admission_no.ilike(pattern), Student.roll_no.ilike(pattern)))
    if session_uuid is not None:
        conditions.append(Student.session_id == session_by_uuid[session_uuid].id)
    if class_uuid is not None:
        conditions.append(Student.class_id == class_by_uuid[class_uuid].id)
    if section_uuid is not None:
        conditions.append(Student.section_id == section_by_uuid[section_uuid].id)

    total = db.execute(select(func.count(Student.id)).where(*conditions)).scalar_one()
    students = db.execute(
        select(Student).options(*_student_options()).where(*conditions)
        .order_by(Student.full_name, Student.id).offset(offset).limit(limit)
    ).scalars().all()
    class_uuid_by_id = {item.id: item.uuid for item in classes}
    return StudentGridResponse(
        rows=[_row(student) for student in students],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(students) < total,
        custom_fields=custom_fields,
        sessions=[StudentGridLookupItem(uuid=item.uuid, name=item.name) for item in sessions],
        classes=[StudentGridLookupItem(uuid=item.uuid, name=item.name) for item in classes],
        sections=[StudentGridLookupItem(uuid=item.uuid, name=item.name, class_uuid=class_uuid_by_id[item.class_id]) for item in sections],
    )


@router.patch("", response_model=StudentGridPatchResponse)
def patch_student_grid(
    school_uuid: UUID,
    payload: StudentGridPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_card_data_access(
        db, current_user, school.id,
        "Only a school administrator or card operator can edit the student grid",
    )
    row_ids = [row.student_uuid for row in payload.rows]
    duplicate_ids = {value for value in row_ids if row_ids.count(value) > 1}
    if duplicate_ids:
        return _error_response([_error(value, "student_uuid", "Student appears more than once") for value in duplicate_ids])

    students = db.execute(
        select(Student).options(*_student_options()).where(
            Student.school_id == school.id,
            Student.is_active.is_(True),
            Student.uuid.in_(row_ids),
        )
    ).scalars().all()
    by_uuid = {student.uuid: student for student in students}
    missing = [value for value in row_ids if value not in by_uuid]
    if missing:
        return _error_response([_error(value, "student_uuid", "Student not found in this school") for value in missing])

    sessions = db.execute(select(AcademicSession).where(AcademicSession.school_id == school.id)).scalars().all()
    classes = db.execute(select(SchoolClass).where(SchoolClass.school_id == school.id)).scalars().all()
    sections = db.execute(select(Section).join(SchoolClass).where(SchoolClass.school_id == school.id)).scalars().all()
    definitions = db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.school_id == school.id,
        CustomFieldDefinition.entity_type == "student",
        CustomFieldDefinition.is_active.is_(True),
    )).scalars().all()
    session_by_uuid = {item.uuid: item for item in sessions}
    class_by_uuid = {item.uuid: item for item in classes}
    section_by_uuid = {item.uuid: item for item in sections}
    definition_by_uuid = {str(item.uuid): item for item in definitions}

    errors: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    plans: list[dict[str, Any]] = []
    for patch in payload.rows:
        student = by_uuid[patch.student_uuid]
        if patch.expected_updated_at is not None and not _same_instant(student.updated_at, patch.expected_updated_at):
            conflicts.append(_error(student.uuid, "updated_at", "This row changed after it was loaded; refresh before saving"))
            continue
        unknown = sorted(set(patch.system_fields) - EDITABLE_SYSTEM_FIELDS)
        errors.extend(_error(student.uuid, field, "Field is not editable in the grid") for field in unknown)

        values = {
            "session_uuid": student.session_uuid,
            "class_uuid": student.class_uuid,
            "section_uuid": student.section_uuid,
            "admission_no": student.admission_no,
            "roll_no": student.roll_no,
            "stream": student.stream,
            "full_name": student.full_name,
            "father_name": student.father_name,
            "mother_name": student.mother_name,
            "dob": student.dob,
            "gender": student.gender,
            "blood_group": student.blood_group,
            "mobile": student.mobile,
            "aadhaar": student.aadhaar,
            "address": student.address,
        }
        for field, raw in patch.system_fields.items():
            if field not in EDITABLE_SYSTEM_FIELDS:
                continue
            if field in {"session_uuid", "class_uuid", "section_uuid"}:
                values[field] = _parse_uuid(field, raw, student.uuid, errors)
            elif field == "dob":
                values[field] = _parse_date(raw, student.uuid, errors)
            elif field == "blood_group":
                cleaned = _clean_text(field, raw, student.uuid, errors)
                if cleaned is not None and cleaned not in {item.value for item in BloodGroup}:
                    errors.append(_error(student.uuid, field, "Select a valid blood group"))
                values[field] = cleaned
            else:
                values[field] = _clean_text(field, raw, student.uuid, errors)

        session = session_by_uuid.get(values["session_uuid"])
        school_class = class_by_uuid.get(values["class_uuid"])
        section = section_by_uuid.get(values["section_uuid"])
        if values["session_uuid"] is not None and session is None:
            errors.append(_error(student.uuid, "session_uuid", "Academic session does not belong to this school"))
        if values["class_uuid"] is not None and school_class is None:
            errors.append(_error(student.uuid, "class_uuid", "Class does not belong to this school"))
        if values["section_uuid"] is not None and section is None:
            errors.append(_error(student.uuid, "section_uuid", "Section does not belong to this school"))
        elif section is not None and school_class is not None and section.class_id != school_class.id:
            errors.append(_error(student.uuid, "section_uuid", "Section does not belong to the selected class"))

        current_custom = {str(item.field_definition.uuid): item.value for item in student.custom_field_values if item.field_definition.is_active}
        audit_custom = {
            item.field_definition.field_key: item.value
            for item in student.custom_field_values
            if item.field_definition.is_active
        }
        merged_custom = dict(current_custom)
        for field_uuid, raw in patch.custom_fields.items():
            definition = definition_by_uuid.get(field_uuid)
            if definition is None:
                errors.append(_error(student.uuid, f"custom_fields.{field_uuid}", "Unknown, inactive, or cross-school custom field"))
                continue
            if raw is not None and not isinstance(raw, (str, int, float)):
                errors.append(_error(student.uuid, f"custom_fields.{field_uuid}", "Value must be text"))
                continue
            merged_custom[field_uuid] = "" if raw is None else str(raw)
        validated_custom = None
        try:
            validated_custom = validate_student_custom_fields(
                db,
                school.id,
                [StudentCustomFieldInput(field_uuid=UUID(key), value=value) for key, value in merged_custom.items()],
                require_all=True,
            )
        except HTTPException as exc:
            field = (
                f"custom_fields.{next(iter(patch.custom_fields))}"
                if len(patch.custom_fields) == 1
                else "custom_fields"
            )
            errors.append(_error(student.uuid, field, str(exc.detail)))
        plans.append({
            "student": student,
            "values": values,
            "custom": validated_custom,
            "current_custom": current_custom,
            "audit_custom": audit_custom,
            "patch": patch,
        })

    if conflicts:
        return _error_response(conflicts, conflict=True)
    if errors:
        return _error_response(errors)

    # Validate uniqueness against the final values before mutating anything.
    all_students = db.execute(select(Student).where(Student.school_id == school.id)).scalars().all()
    plan_by_id = {plan["student"].id: plan for plan in plans}
    admissions: dict[str, list[tuple[Student, bool]]] = {}
    rolls: dict[tuple[int, int, str], list[tuple[Student, bool]]] = {}
    for item in all_students:
        plan = plan_by_id.get(item.id)
        values = plan["values"] if plan is not None else None
        admission = values["admission_no"] if values is not None else item.admission_no
        session_id = session_by_uuid[values["session_uuid"]].id if values is not None else item.session_id
        class_id = class_by_uuid[values["class_uuid"]].id if values is not None else item.class_id
        roll = values["roll_no"] if values is not None else item.roll_no
        admissions.setdefault(admission, []).append((item, plan is not None))
        if roll is not None:
            key = (session_id, class_id, roll)
            rolls.setdefault(key, []).append((item, plan is not None))
    for duplicates in admissions.values():
        if len(duplicates) > 1:
            for item, is_patched in duplicates:
                if is_patched:
                    errors.append(_error(item.uuid, "admission_no", "Admission number already exists in this school"))
    for duplicates in rolls.values():
        if len(duplicates) > 1:
            for item, is_patched in duplicates:
                if is_patched:
                    errors.append(_error(item.uuid, "roll_no", "Roll number already exists for this class in this academic session"))
    if errors:
        return _error_response(errors)

    changed_students: list[Student] = []
    try:
        for plan in plans:
            student = plan["student"]
            values = plan["values"]
            before = {
                "session_id": student.session_id,
                "class_id": student.class_id,
                "section_id": student.section_id,
                **{field: getattr(student, field) for field in EDITABLE_SYSTEM_FIELDS if not field.endswith("_uuid")},
            }
            student.session_id = session_by_uuid[values["session_uuid"]].id
            student.class_id = class_by_uuid[values["class_uuid"]].id
            student.section_id = section_by_uuid[values["section_uuid"]].id
            for field in EDITABLE_SYSTEM_FIELDS - {"session_uuid", "class_uuid", "section_uuid"}:
                setattr(student, field, values[field])

            validated = plan["custom"] or []
            existing = {str(item.field_definition.uuid): item for item in student.custom_field_values if item.field_definition.is_active}
            normalized = {str(definition.uuid): value for definition, value in validated}
            for field_uuid in plan["patch"].custom_fields:
                value = normalized[field_uuid]
                item = existing.get(field_uuid)
                if item is None:
                    student.custom_field_values.append(StudentCustomFieldValue(field_definition=definition_by_uuid[field_uuid], value=value))
                else:
                    item.value = value

            changes = {
                field: (old, getattr(student, field))
                for field, old in before.items()
                if old != getattr(student, field)
            }
            after_custom = dict(plan["audit_custom"])
            after_custom.update({
                definition_by_uuid[key].field_key: normalized[key]
                for key in plan["patch"].custom_fields
            })
            changes.update(custom_field_change_set(plan["audit_custom"], after_custom))
            if changes:
                student.updated_at = datetime.now(timezone.utc)
                record_student_field_changes(db, student=student, actor=current_user, changes=changes)
                changed_students.append(student)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _error_response([{"student_uuid": "", "field": "grid", "message": "Student data changed while saving; refresh and try again"}], conflict=True)
    except Exception:
        db.rollback()
        raise

    if changed_students:
        refreshed = db.execute(
            select(Student).options(*_student_options()).where(Student.id.in_([item.id for item in changed_students]))
        ).scalars().all()
    else:
        refreshed = []
    refreshed_by_uuid = {item.uuid: item for item in refreshed}
    ordered = [refreshed_by_uuid[item.student_uuid] for item in payload.rows if item.student_uuid in refreshed_by_uuid]
    return StudentGridPatchResponse(updated_count=len(ordered), rows=[_row(item) for item in ordered])
