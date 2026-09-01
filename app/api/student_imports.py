import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.custom_fields import replace_student_custom_fields, validate_student_custom_fields
from app.core.database import get_db
from app.core.school_access import get_active_school, require_card_data_access
from app.core.security import get_current_user
from app.core.student_imports import delete_import_manifest, load_import_manifest, parse_student_upload, save_import_manifest
from app.core.student_import_template import (
    XLSX_CONTENT_TYPE,
    build_student_import_template,
    student_import_template_filename,
)
from app.core.student_audit import record_student_audit
from app.models.academic_session import AcademicSession
from app.models.custom_field import CustomFieldDefinition
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.users import User
from app.schemas.student import StudentCreate, StudentCustomFieldInput
from app.schemas.student_import import (
    StudentImportCommitRequest,
    StudentImportField,
    StudentImportMapping,
    StudentImportMappingItem,
    StudentImportPreviewResponse,
    StudentImportRowPreview,
    StudentImportSummary,
    StudentImportUploadResponse,
)


router = APIRouter(prefix="/schools/{school_uuid}/students/imports", tags=["Student Imports"])

BUILT_IN_FIELDS = (
    StudentImportField(key="academic_session", label="Academic Session", required=True),
    StudentImportField(key="class", label="Class", required=True),
    StudentImportField(key="section", label="Section", required=True),
    StudentImportField(key="admission_no", label="Admission Number", required=True),
    StudentImportField(key="roll_no", label="Roll Number"),
    StudentImportField(key="stream", label="Stream"),
    StudentImportField(key="full_name", label="Full Name", required=True),
    StudentImportField(key="father_name", label="Father Name"),
    StudentImportField(key="mother_name", label="Mother Name"),
    StudentImportField(key="dob", label="Date of Birth", data_type="date"),
    StudentImportField(key="gender", label="Gender"),
    StudentImportField(key="blood_group", label="Blood Group"),
    StudentImportField(key="mobile", label="Mobile", data_type="phone"),
    StudentImportField(key="aadhaar", label="Aadhaar"),
    StudentImportField(key="address", label="Address", data_type="multiline"),
)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _target_fields(definitions: list[CustomFieldDefinition]) -> list[StudentImportField]:
    return [
        *BUILT_IN_FIELDS,
        *[
            StudentImportField(
                key=f"custom:{definition.uuid}",
                label=definition.label,
                required=definition.is_required,
                data_type=definition.data_type,
                custom_field_uuid=definition.uuid,
            )
            for definition in definitions
        ],
    ]


def _suggest_mappings(headers: list[str], fields: list[StudentImportField]) -> list[StudentImportMappingItem]:
    by_name: dict[str, list[str]] = {}
    for header in headers:
        by_name.setdefault(_normalized_name(header), []).append(header)
    suggestions = []
    used_sources = set()
    for field in fields:
        candidates = {
            _normalized_name(field.label),
            _normalized_name(field.key.split(":", 1)[0]),
        }
        if field.key == "academic_session":
            candidates.add("session")
        matches = [header for candidate in candidates for header in by_name.get(candidate, [])]
        if len(set(matches)) == 1 and matches[0] not in used_sources:
            suggestions.append(StudentImportMappingItem(source_column=matches[0], target_field=field.key))
            used_sources.add(matches[0])
    return suggestions


def _active_custom_fields(db: Session, school_id: int) -> list[CustomFieldDefinition]:
    return db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.school_id == school_id,
            CustomFieldDefinition.entity_type == "student",
            CustomFieldDefinition.is_active.is_(True),
        ).order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)
    ).scalars().all()


def _resolve_target_fields(db: Session, school_id: int) -> list[StudentImportField]:
    """Return the ordered, currently importable schema for one school."""
    return _target_fields(_active_custom_fields(db, school_id))


def _unique_lookup(items: list[Any], label: str) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    for item in items:
        grouped.setdefault(item.name.strip().casefold(), []).append(item)
    ambiguous = [values[0].name for values in grouped.values() if len(values) > 1]
    if ambiguous:
        raise HTTPException(status_code=409, detail=f"Ambiguous {label} names in school configuration: {ambiguous[0]}")
    return {key: values[0] for key, values in grouped.items()}


@dataclass
class _ValidatedImportRow:
    preview: StudentImportRowPreview
    student_data: StudentCreate | None = None
    session_id: int | None = None
    class_id: int | None = None
    section_id: int | None = None
    custom_fields: list[tuple[CustomFieldDefinition, str]] | None = None


def _validate_import(
    db: Session,
    school_id: int,
    upload_id: UUID,
    manifest: dict[str, Any],
    payload: StudentImportMapping,
) -> tuple[StudentImportPreviewResponse, list[_ValidatedImportRow]]:
    headers = set(manifest["headers"])
    fields = _resolve_target_fields(db, school_id)
    valid_targets = {field.key for field in fields}
    mapping = {item.target_field: item.source_column for item in payload.mappings}
    unknown_sources = [source for source in mapping.values() if source not in headers]
    unknown_targets = [target for target in mapping if target not in valid_targets]
    if unknown_sources:
        raise HTTPException(status_code=422, detail=f"Unknown spreadsheet column: {unknown_sources[0]}")
    if unknown_targets:
        raise HTTPException(status_code=422, detail=f"Unknown or inactive target field: {unknown_targets[0]}")
    missing = [field.label for field in fields if field.required and field.key not in mapping]
    if missing:
        raise HTTPException(status_code=422, detail=f"Required target is not mapped: {missing[0]}")

    sessions = db.execute(select(AcademicSession).where(AcademicSession.school_id == school_id)).scalars().all()
    classes = db.execute(select(SchoolClass).where(SchoolClass.school_id == school_id)).scalars().all()
    sections = db.execute(select(Section).join(SchoolClass).where(SchoolClass.school_id == school_id)).scalars().all()
    session_lookup = _unique_lookup(sessions, "academic session")
    class_lookup = _unique_lookup(classes, "class")
    section_lookup: dict[tuple[int, str], Section] = {}
    for section in sections:
        key = (section.class_id, section.name.strip().casefold())
        if key in section_lookup:
            raise HTTPException(status_code=409, detail=f"Ambiguous section names in class: {section.name}")
        section_lookup[key] = section

    existing_rows = db.execute(
        select(Student.admission_no, Student.session_id, Student.class_id, Student.roll_no).where(Student.school_id == school_id)
    ).all()
    existing_admissions = {row[0] for row in existing_rows}
    existing_rolls = {(row[1], row[2], row[3]) for row in existing_rows if row[3] is not None}
    seen_admissions: set[str] = set()
    seen_rolls: set[tuple[int, int, str]] = set()
    validated_rows = []
    duplicate_rows = 0

    for index, raw_row in enumerate(manifest["rows"], start=2):
        values = {target: raw_row[source].strip() for target, source in mapping.items()}
        errors: list[str] = []
        session = session_lookup.get(values.get("academic_session", "").casefold())
        school_class = class_lookup.get(values.get("class", "").casefold())
        section = section_lookup.get((school_class.id, values.get("section", "").casefold())) if school_class else None
        if session is None:
            errors.append(f"Academic session not found: {values.get('academic_session', '')}")
        if school_class is None:
            errors.append(f"Class not found: {values.get('class', '')}")
        if school_class is not None and section is None:
            errors.append(f"Section not found in class: {values.get('section', '')}")
        admission_no = values.get("admission_no", "")
        full_name = values.get("full_name", "")
        if not admission_no:
            errors.append("Admission Number is required")
        if not full_name:
            errors.append("Full Name is required")
        row_is_duplicate = False
        if admission_no:
            if admission_no in seen_admissions:
                errors.append("Duplicate admission number within upload")
                row_is_duplicate = True
            elif admission_no in existing_admissions:
                errors.append("Admission number already exists in this school")
                row_is_duplicate = True
            seen_admissions.add(admission_no)

        roll_no = values.get("roll_no") or None
        if roll_no and session and school_class:
            roll_key = (session.id, school_class.id, roll_no)
            if roll_key in seen_rolls:
                errors.append("Duplicate roll number within upload for session and class")
                row_is_duplicate = True
            elif roll_key in existing_rolls:
                errors.append("Roll number already exists for this class in this academic session")
                row_is_duplicate = True
            seen_rolls.add(roll_key)
        if row_is_duplicate:
            duplicate_rows += 1

        custom_inputs = [
            StudentCustomFieldInput(field_uuid=UUID(target.split(":", 1)[1]), value=value)
            for target, value in values.items()
            if target.startswith("custom:")
        ]
        student_data = None
        validated_custom = None
        if session and school_class and section:
            try:
                student_data = StudentCreate(
                    session_uuid=session.uuid,
                    class_uuid=school_class.uuid,
                    section_uuid=section.uuid,
                    admission_no=admission_no,
                    roll_no=roll_no,
                    stream=values.get("stream") or None,
                    full_name=full_name,
                    father_name=values.get("father_name") or None,
                    mother_name=values.get("mother_name") or None,
                    dob=values.get("dob") or None,
                    gender=values.get("gender") or None,
                    blood_group=values.get("blood_group") or None,
                    mobile=values.get("mobile") or None,
                    aadhaar=values.get("aadhaar") or None,
                    address=values.get("address") or None,
                    custom_fields=custom_inputs,
                )
                validated_custom = validate_student_custom_fields(db, school_id, custom_inputs, require_all=True)
            except (ValidationError, HTTPException) as exc:
                if isinstance(exc, ValidationError):
                    errors.extend(error["msg"] for error in exc.errors())
                else:
                    errors.append(str(exc.detail))
        preview = StudentImportRowPreview(row_number=index, values=values, errors=errors)
        validated_rows.append(_ValidatedImportRow(preview, student_data, session.id if session else None, school_class.id if school_class else None, section.id if section else None, validated_custom))

    invalid = sum(bool(row.preview.errors) for row in validated_rows)
    response = StudentImportPreviewResponse(
        upload_id=upload_id,
        total_rows=len(validated_rows),
        valid_rows=len(validated_rows) - invalid,
        invalid_rows=invalid,
        duplicate_rows=duplicate_rows,
        can_import=invalid == 0,
        rows=[row.preview for row in validated_rows],
    )
    return response, validated_rows


def _authorize(db: Session, current_user: User, school_uuid: UUID):
    school = get_active_school(db, school_uuid)
    require_card_data_access(db, current_user, school.id, "Only a school administrator or card operator can import students")
    return school


@router.post("/upload", response_model=StudentImportUploadResponse, status_code=201)
async def upload_student_import(school_uuid: UUID, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = _authorize(db, current_user, school_uuid)
    headers, rows = await parse_student_upload(file)
    fields = _resolve_target_fields(db, school.id)
    upload_id = save_import_manifest(school_uuid=school_uuid, user_id=current_user.id, filename=file.filename or "students", headers=headers, rows=rows)
    return StudentImportUploadResponse(upload_id=upload_id, filename=file.filename or "students", headers=headers, row_count=len(rows), target_fields=fields, suggested_mappings=_suggest_mappings(headers, fields))


@router.get("/template")
def download_student_import_template(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    fields = _resolve_target_fields(db, school.id)
    filename = student_import_template_filename(school.school_name)
    return Response(
        content=build_student_import_template(fields),
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{upload_id}/preview", response_model=StudentImportPreviewResponse)
def preview_student_import(school_uuid: UUID, upload_id: UUID, payload: StudentImportMapping, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = _authorize(db, current_user, school_uuid)
    manifest = load_import_manifest(upload_id=upload_id, school_uuid=school_uuid, user_id=current_user.id)
    response, _ = _validate_import(db, school.id, upload_id, manifest, payload)
    return response


@router.post("/{upload_id}/commit", response_model=StudentImportSummary, status_code=201)
def commit_student_import(school_uuid: UUID, upload_id: UUID, payload: StudentImportCommitRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")
    school = _authorize(db, current_user, school_uuid)
    manifest = load_import_manifest(upload_id=upload_id, school_uuid=school_uuid, user_id=current_user.id)
    preview, rows = _validate_import(db, school.id, upload_id, manifest, payload)
    if not preview.can_import:
        raise HTTPException(status_code=422, detail=preview.model_dump(mode="json"))
    try:
        students = []
        for row in rows:
            data = row.student_data
            student = Student(
                school_id=school.id, session_id=row.session_id, class_id=row.class_id, section_id=row.section_id,
                admission_no=data.admission_no, roll_no=data.roll_no, stream=data.stream, full_name=data.full_name,
                father_name=data.father_name, mother_name=data.mother_name, dob=data.dob, gender=data.gender,
                blood_group=data.blood_group, mobile=data.mobile, aadhaar=data.aadhaar, address=data.address, photo_path=None,
            )
            db.add(student)
            replace_student_custom_fields(db, student, row.custom_fields or [])
            students.append(student)
        db.flush()
        for student in students:
            record_student_audit(
                db,
                student=student,
                actor=current_user,
                event_type="student_created",
                new_value={"admission_no": student.admission_no, "full_name": student.full_name},
                note="Created by bulk student import",
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Import conflicted with student data changed after preview; no students were imported") from exc
    except Exception:
        db.rollback()
        raise
    delete_import_manifest(upload_id)
    return StudentImportSummary(upload_id=upload_id, imported_count=len(rows), message=f"Imported {len(rows)} students")
