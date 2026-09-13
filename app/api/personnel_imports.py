import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.custom_fields import (
    replace_personnel_custom_fields,
    validate_personnel_custom_fields,
)
from app.core.database import get_db
from app.core.personnel_audit import record_personnel_audit
from app.core.school_access import get_active_school, require_identity_data_access
from app.core.security import get_current_user
from app.core.student_import_template import XLSX_CONTENT_TYPE, build_student_import_template
from app.core.student_imports import (
    delete_import_manifest,
    load_import_manifest,
    parse_student_upload,
    save_import_manifest,
)
from app.models.custom_field import CustomFieldDefinition
from app.models.personnel import Personnel
from app.models.users import User
from app.schemas.personnel import PersonnelCreate, PersonnelType
from app.schemas.personnel_import import (
    PersonnelImportCommitRequest,
    PersonnelImportField,
    PersonnelImportMapping,
    PersonnelImportMappingItem,
    PersonnelImportPreviewResponse,
    PersonnelImportRowPreview,
    PersonnelImportSummary,
    PersonnelImportUploadResponse,
)
from app.schemas.student import StudentCustomFieldInput


router = APIRouter(
    prefix="/schools/{school_uuid}/personnel/imports",
    tags=["Personnel Imports"],
)

BUILT_IN_FIELDS = (
    PersonnelImportField(key="employee_no", label="Employee Number", required=True),
    PersonnelImportField(key="full_name", label="Full Name", required=True),
    PersonnelImportField(key="designation", label="Designation"),
    PersonnelImportField(key="department", label="Department"),
    PersonnelImportField(key="dob", label="Date of Birth", data_type="date"),
    PersonnelImportField(key="gender", label="Gender"),
    PersonnelImportField(key="blood_group", label="Blood Group"),
    PersonnelImportField(key="mobile", label="Mobile", data_type="phone"),
    PersonnelImportField(key="email", label="Email"),
    PersonnelImportField(key="address", label="Address", data_type="multiline"),
)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _active_custom_fields(
    db: Session,
    school_id: int,
    personnel_type: PersonnelType,
) -> list[CustomFieldDefinition]:
    return db.execute(
        select(CustomFieldDefinition)
        .where(
            CustomFieldDefinition.school_id == school_id,
            CustomFieldDefinition.entity_type == personnel_type.value,
            CustomFieldDefinition.is_active.is_(True),
        )
        .order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)
    ).scalars().all()


def _target_fields(
    definitions: list[CustomFieldDefinition],
) -> list[PersonnelImportField]:
    return [
        *BUILT_IN_FIELDS,
        *[
            PersonnelImportField(
                key=f"custom:{definition.uuid}",
                label=definition.label,
                required=definition.is_required,
                data_type=definition.data_type,
                custom_field_uuid=definition.uuid,
            )
            for definition in definitions
        ],
    ]


def _resolve_target_fields(
    db: Session,
    school_id: int,
    personnel_type: PersonnelType,
) -> list[PersonnelImportField]:
    return _target_fields(_active_custom_fields(db, school_id, personnel_type))


def _suggest_mappings(
    headers: list[str],
    fields: list[PersonnelImportField],
) -> list[PersonnelImportMappingItem]:
    by_name: dict[str, list[str]] = {}
    for header in headers:
        by_name.setdefault(_normalized_name(header), []).append(header)
    suggestions: list[PersonnelImportMappingItem] = []
    used_sources: set[str] = set()
    aliases = {"employee_no": {"employeeno", "employeenumber", "staffid"}}
    for field in fields:
        candidates = {
            _normalized_name(field.label),
            _normalized_name(field.key.split(":", 1)[0]),
            *aliases.get(field.key, set()),
        }
        matches = [
            header
            for candidate in candidates
            for header in by_name.get(candidate, [])
        ]
        if len(set(matches)) == 1 and matches[0] not in used_sources:
            suggestions.append(
                PersonnelImportMappingItem(
                    source_column=matches[0],
                    target_field=field.key,
                )
            )
            used_sources.add(matches[0])
    return suggestions


def _purpose(personnel_type: PersonnelType) -> str:
    return f"personnel:{personnel_type.value}"


def _authorize(db: Session, current_user: User, school_uuid: UUID):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can import personnel",
    )
    return school


@dataclass
class _ValidatedImportRow:
    preview: PersonnelImportRowPreview
    personnel_data: PersonnelCreate | None = None
    custom_fields: list[tuple[CustomFieldDefinition, str]] | None = None


def _validate_import(
    db: Session,
    school_id: int,
    upload_id: UUID,
    personnel_type: PersonnelType,
    manifest: dict[str, Any],
    payload: PersonnelImportMapping,
) -> tuple[PersonnelImportPreviewResponse, list[_ValidatedImportRow]]:
    headers = set(manifest["headers"])
    definitions = _active_custom_fields(db, school_id, personnel_type)
    fields = _target_fields(definitions)
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

    existing_numbers = {
        value.casefold()
        for value in
        db.execute(
            select(Personnel.employee_no).where(Personnel.school_id == school_id)
        ).scalars().all()
    }
    seen_numbers: set[str] = set()
    validated_rows: list[_ValidatedImportRow] = []
    duplicate_rows = 0

    for index, raw_row in enumerate(manifest["rows"], start=2):
        values = {target: raw_row[source].strip() for target, source in mapping.items()}
        errors: list[str] = []
        employee_no = values.get("employee_no", "")
        full_name = values.get("full_name", "")
        if not employee_no:
            errors.append("Employee Number is required")
        if not full_name:
            errors.append("Full Name is required")
        row_duplicate = False
        if employee_no:
            employee_key = employee_no.casefold()
            if employee_key in seen_numbers:
                errors.append("Duplicate employee number within upload")
                row_duplicate = True
            elif employee_key in existing_numbers:
                errors.append("Employee number already exists in this school")
                row_duplicate = True
            seen_numbers.add(employee_key)
        if row_duplicate:
            duplicate_rows += 1

        custom_inputs = [
            StudentCustomFieldInput(
                field_uuid=UUID(target.split(":", 1)[1]),
                value=value,
            )
            for target, value in values.items()
            if target.startswith("custom:")
        ]
        personnel_data = None
        validated_custom = None
        try:
            personnel_data = PersonnelCreate(
                personnel_type=personnel_type,
                employee_no=employee_no,
                full_name=full_name,
                designation=values.get("designation") or None,
                department=values.get("department") or None,
                dob=values.get("dob") or None,
                gender=values.get("gender") or None,
                blood_group=values.get("blood_group") or None,
                mobile=values.get("mobile") or None,
                email=values.get("email") or None,
                address=values.get("address") or None,
                custom_fields=custom_inputs,
            )
            validated_custom = validate_personnel_custom_fields(
                db,
                school_id,
                personnel_type.value,
                custom_inputs,
                require_all=True,
                definitions=definitions,
            )
        except (ValidationError, HTTPException) as exc:
            if isinstance(exc, ValidationError):
                errors.extend(error["msg"] for error in exc.errors())
            else:
                errors.append(str(exc.detail))

        preview = PersonnelImportRowPreview(
            row_number=index,
            values=values,
            errors=errors,
        )
        validated_rows.append(
            _ValidatedImportRow(preview, personnel_data, validated_custom)
        )

    invalid = sum(bool(row.preview.errors) for row in validated_rows)
    response = PersonnelImportPreviewResponse(
        upload_id=upload_id,
        personnel_type=personnel_type,
        total_rows=len(validated_rows),
        valid_rows=len(validated_rows) - invalid,
        invalid_rows=invalid,
        duplicate_rows=duplicate_rows,
        can_import=invalid == 0,
        rows=[row.preview for row in validated_rows],
    )
    return response, validated_rows


@router.post("/upload", response_model=PersonnelImportUploadResponse, status_code=201)
async def upload_personnel_import(
    school_uuid: UUID,
    personnel_type: PersonnelType = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    headers, rows = await parse_student_upload(file, entity_label="personnel")
    fields = _resolve_target_fields(db, school.id, personnel_type)
    upload_id = save_import_manifest(
        school_uuid=school_uuid,
        user_id=current_user.id,
        filename=file.filename or f"{personnel_type.value}s",
        headers=headers,
        rows=rows,
        purpose=_purpose(personnel_type),
    )
    return PersonnelImportUploadResponse(
        upload_id=upload_id,
        filename=file.filename or f"{personnel_type.value}s",
        personnel_type=personnel_type,
        headers=headers,
        row_count=len(rows),
        target_fields=fields,
        suggested_mappings=_suggest_mappings(headers, fields),
    )


@router.get("/template")
def download_personnel_import_template(
    school_uuid: UUID,
    personnel_type: PersonnelType = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    fields = _resolve_target_fields(db, school.id, personnel_type)
    safe_school = re.sub(r"[^a-z0-9]+", "_", school.school_name.casefold()).strip("_")
    filename = f"{personnel_type.value}_import_template_{safe_school or 'school'}.xlsx"
    return Response(
        content=build_student_import_template(
            fields,
            sheet_name="Teachers" if personnel_type == PersonnelType.TEACHER else "Staff",
            entity_label="personnel",
        ),
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _manifest(
    *,
    upload_id: UUID,
    school_uuid: UUID,
    user_id: int,
    personnel_type: PersonnelType,
) -> dict[str, Any]:
    return load_import_manifest(
        upload_id=upload_id,
        school_uuid=school_uuid,
        user_id=user_id,
        purpose=_purpose(personnel_type),
    )


@router.post("/{upload_id}/preview", response_model=PersonnelImportPreviewResponse)
def preview_personnel_import(
    school_uuid: UUID,
    upload_id: UUID,
    personnel_type: PersonnelType,
    payload: PersonnelImportMapping,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    manifest = _manifest(
        upload_id=upload_id,
        school_uuid=school_uuid,
        user_id=current_user.id,
        personnel_type=personnel_type,
    )
    response, _ = _validate_import(
        db, school.id, upload_id, personnel_type, manifest, payload
    )
    return response


@router.post("/{upload_id}/commit", response_model=PersonnelImportSummary, status_code=201)
def commit_personnel_import(
    school_uuid: UUID,
    upload_id: UUID,
    personnel_type: PersonnelType,
    payload: PersonnelImportCommitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")
    school = _authorize(db, current_user, school_uuid)
    manifest = _manifest(
        upload_id=upload_id,
        school_uuid=school_uuid,
        user_id=current_user.id,
        personnel_type=personnel_type,
    )
    preview, rows = _validate_import(
        db, school.id, upload_id, personnel_type, manifest, payload
    )
    if not preview.can_import:
        raise HTTPException(status_code=422, detail=preview.model_dump(mode="json"))
    try:
        created: list[Personnel] = []
        for row in rows:
            data = row.personnel_data
            if data is None:
                raise RuntimeError("Validated personnel row is missing data")
            personnel = Personnel(
                school_id=school.id,
                personnel_type=personnel_type.value,
                employee_no=data.employee_no,
                full_name=data.full_name,
                designation=data.designation,
                department=data.department,
                dob=data.dob,
                gender=data.gender,
                blood_group=data.blood_group.value if data.blood_group else None,
                mobile=data.mobile,
                email=data.email,
                address=data.address,
                photo_path=None,
            )
            db.add(personnel)
            replace_personnel_custom_fields(db, personnel, row.custom_fields or [])
            created.append(personnel)
        db.flush()
        for personnel in created:
            record_personnel_audit(
                db,
                personnel=personnel,
                actor=current_user,
                event_type="personnel_created",
                new_value={
                    "personnel_type": personnel.personnel_type,
                    "employee_no": personnel.employee_no,
                    "full_name": personnel.full_name,
                },
                note="Created by bulk personnel import",
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Import conflicted with personnel data changed after preview; no personnel were imported",
        ) from exc
    except Exception:
        db.rollback()
        raise
    delete_import_manifest(upload_id)
    label = "teachers" if personnel_type == PersonnelType.TEACHER else "staff"
    return PersonnelImportSummary(
        upload_id=upload_id,
        personnel_type=personnel_type,
        imported_count=len(rows),
        message=f"Imported {len(rows)} {label}",
    )
