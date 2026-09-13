from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.custom_fields import validate_personnel_custom_fields
from app.core.database import get_db
from app.core.personnel_audit import record_personnel_field_changes
from app.core.school_access import get_active_school, require_identity_data_access
from app.core.security import get_current_user
from app.core.student_audit import custom_field_change_set
from app.models.custom_field import CustomFieldDefinition, PersonnelCustomFieldValue
from app.models.personnel import Personnel
from app.models.users import User
from app.schemas.personnel import PersonnelType
from app.schemas.personnel_grid import (
    PersonnelGridPatchRequest,
    PersonnelGridPatchResponse,
    PersonnelGridResponse,
    PersonnelGridRow,
)
from app.schemas.student import BloodGroup, StudentCustomFieldInput


router = APIRouter(prefix="/schools/{school_uuid}/personnel/grid", tags=["Personnel Grid"])

EDITABLE_SYSTEM_FIELDS = frozenset(
    {
        "employee_no",
        "full_name",
        "designation",
        "department",
        "dob",
        "gender",
        "blood_group",
        "mobile",
        "email",
        "address",
    }
)
REQUIRED_SYSTEM_FIELDS = frozenset({"employee_no", "full_name"})
TEXT_LIMITS = {
    "employee_no": 50,
    "full_name": 150,
    "designation": 120,
    "department": 120,
    "gender": 20,
    "blood_group": 5,
    "mobile": 20,
    "email": 150,
    "address": 2000,
}


def _options():
    return (
        selectinload(Personnel.custom_field_values).selectinload(
            PersonnelCustomFieldValue.field_definition
        ),
    )


def _row(personnel: Personnel) -> PersonnelGridRow:
    return PersonnelGridRow(
        uuid=personnel.uuid,
        updated_at=personnel.updated_at,
        personnel_type=personnel.personnel_type,
        employee_no=personnel.employee_no,
        full_name=personnel.full_name,
        designation=personnel.designation,
        department=personnel.department,
        dob=personnel.dob,
        gender=personnel.gender,
        blood_group=personnel.blood_group,
        mobile=personnel.mobile,
        email=personnel.email,
        address=personnel.address,
        is_active=personnel.is_active,
        custom_fields={
            str(value.field_definition.uuid): value.value
            for value in personnel.custom_field_values
            if value.field_definition.is_active
        },
    )


def _error(personnel_uuid: UUID, field: str, message: str) -> dict[str, str]:
    return {
        "personnel_uuid": str(personnel_uuid),
        "field": field,
        "message": message,
    }


def _error_response(errors: list[dict[str, str]], *, conflict: bool = False):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": "Grid conflict detected" if conflict else "Grid validation failed",
            "errors": errors,
        },
    )


def _clean_text(
    field: str,
    value: Any,
    personnel_uuid: UUID,
    errors: list[dict[str, str]],
) -> str | None:
    if value is not None and not isinstance(value, str):
        errors.append(_error(personnel_uuid, field, "Must be text"))
        return None
    cleaned = value.strip() if isinstance(value, str) else None
    if not cleaned:
        if field in REQUIRED_SYSTEM_FIELDS:
            errors.append(_error(personnel_uuid, field, "This field is required"))
        return None
    limit = TEXT_LIMITS[field]
    if len(cleaned) > limit:
        errors.append(_error(personnel_uuid, field, f"Must be {limit} characters or fewer"))
    return cleaned


def _parse_date(
    value: Any,
    personnel_uuid: UUID,
    errors: list[dict[str, str]],
) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(_error(personnel_uuid, "dob", "Must be a valid YYYY-MM-DD date"))
        return None


def _same_instant(left: datetime, right: datetime) -> bool:
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    return aware(left).astimezone(timezone.utc) == aware(right).astimezone(timezone.utc)


@router.get("", response_model=PersonnelGridResponse)
def get_personnel_grid(
    school_uuid: UUID,
    personnel_type: PersonnelType,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=150),
    active: bool | None = True,
    department: str | None = Query(default=None, max_length=120),
    designation: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can use the personnel grid",
    )
    custom_fields = db.execute(
        select(CustomFieldDefinition)
        .where(
            CustomFieldDefinition.school_id == school.id,
            CustomFieldDefinition.entity_type == personnel_type.value,
            CustomFieldDefinition.is_active.is_(True),
        )
        .order_by(CustomFieldDefinition.display_order, CustomFieldDefinition.id)
    ).scalars().all()

    base = [
        Personnel.school_id == school.id,
        Personnel.personnel_type == personnel_type.value,
    ]
    lookup_rows = db.execute(
        select(Personnel.department, Personnel.designation).where(*base)
    ).all()
    departments = sorted({row[0] for row in lookup_rows if row[0]})
    designations = sorted({row[1] for row in lookup_rows if row[1]})

    conditions = list(base)
    if active is not None:
        conditions.append(Personnel.is_active.is_(active))
    if department and department.strip():
        conditions.append(Personnel.department == department.strip())
    if designation and designation.strip():
        conditions.append(Personnel.designation == designation.strip())
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                Personnel.full_name.ilike(pattern),
                Personnel.employee_no.ilike(pattern),
                Personnel.designation.ilike(pattern),
                Personnel.department.ilike(pattern),
            )
        )

    total = db.execute(select(func.count(Personnel.id)).where(*conditions)).scalar_one()
    rows = db.execute(
        select(Personnel)
        .options(*_options())
        .where(*conditions)
        .order_by(Personnel.full_name, Personnel.id)
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return PersonnelGridResponse(
        rows=[_row(item) for item in rows],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(rows) < total,
        personnel_type=personnel_type,
        custom_fields=custom_fields,
        departments=departments,
        designations=designations,
    )


@router.patch("", response_model=PersonnelGridPatchResponse)
def patch_personnel_grid(
    school_uuid: UUID,
    payload: PersonnelGridPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can edit the personnel grid",
    )
    row_ids = [row.personnel_uuid for row in payload.rows]
    duplicate_ids = {value for value in row_ids if row_ids.count(value) > 1}
    if duplicate_ids:
        return _error_response(
            [_error(value, "personnel_uuid", "Personnel appears more than once") for value in duplicate_ids]
        )

    personnel_rows = db.execute(
        select(Personnel)
        .options(*_options())
        .where(Personnel.school_id == school.id, Personnel.uuid.in_(row_ids))
    ).scalars().all()
    by_uuid = {item.uuid: item for item in personnel_rows}
    missing = [value for value in row_ids if value not in by_uuid]
    if missing:
        return _error_response(
            [_error(value, "personnel_uuid", "Personnel not found in this school") for value in missing]
        )

    definitions = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.school_id == school.id,
            CustomFieldDefinition.entity_type.in_(["teacher", "staff"]),
            CustomFieldDefinition.is_active.is_(True),
        )
    ).scalars().all()
    definition_by_uuid = {str(item.uuid): item for item in definitions}
    errors: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    plans: list[dict[str, Any]] = []

    for patch in payload.rows:
        personnel = by_uuid[patch.personnel_uuid]
        if patch.expected_updated_at is not None and not _same_instant(
            personnel.updated_at, patch.expected_updated_at
        ):
            conflicts.append(
                _error(
                    personnel.uuid,
                    "updated_at",
                    "This row changed after it was loaded; refresh before saving",
                )
            )
            continue
        unknown = sorted(set(patch.system_fields) - EDITABLE_SYSTEM_FIELDS)
        errors.extend(
            _error(personnel.uuid, field, "Field is not editable in the grid")
            for field in unknown
        )
        values = {field: getattr(personnel, field) for field in EDITABLE_SYSTEM_FIELDS}
        for field, raw in patch.system_fields.items():
            if field not in EDITABLE_SYSTEM_FIELDS:
                continue
            if field == "dob":
                values[field] = _parse_date(raw, personnel.uuid, errors)
            else:
                cleaned = _clean_text(field, raw, personnel.uuid, errors)
                if field == "blood_group" and cleaned is not None:
                    if cleaned not in {item.value for item in BloodGroup}:
                        errors.append(_error(personnel.uuid, field, "Select a valid blood group"))
                values[field] = cleaned

        current_custom = {
            str(item.field_definition.uuid): item.value
            for item in personnel.custom_field_values
            if item.field_definition.is_active
        }
        audit_custom = {
            item.field_definition.field_key: item.value
            for item in personnel.custom_field_values
            if item.field_definition.is_active
        }
        merged_custom = dict(current_custom)
        for field_uuid, raw in patch.custom_fields.items():
            definition = definition_by_uuid.get(field_uuid)
            if definition is None or definition.entity_type != personnel.personnel_type:
                errors.append(
                    _error(
                        personnel.uuid,
                        f"custom_fields.{field_uuid}",
                        "Unknown, inactive, cross-school, or wrong-type custom field",
                    )
                )
                continue
            if raw is not None and not isinstance(raw, (str, int, float)):
                errors.append(
                    _error(personnel.uuid, f"custom_fields.{field_uuid}", "Value must be text")
                )
                continue
            merged_custom[field_uuid] = "" if raw is None else str(raw)
        validated_custom = None
        try:
            validated_custom = validate_personnel_custom_fields(
                db,
                school.id,
                personnel.personnel_type,
                [
                    StudentCustomFieldInput(field_uuid=UUID(key), value=value)
                    for key, value in merged_custom.items()
                ],
                require_all=True,
                definitions=definitions,
            )
        except HTTPException as exc:
            field = (
                f"custom_fields.{next(iter(patch.custom_fields))}"
                if len(patch.custom_fields) == 1
                else "custom_fields"
            )
            errors.append(_error(personnel.uuid, field, str(exc.detail)))
        plans.append(
            {
                "personnel": personnel,
                "values": values,
                "custom": validated_custom,
                "audit_custom": audit_custom,
                "patch": patch,
            }
        )

    if conflicts:
        return _error_response(conflicts, conflict=True)
    if errors:
        return _error_response(errors)

    all_personnel = db.execute(
        select(Personnel).where(Personnel.school_id == school.id)
    ).scalars().all()
    plan_by_id = {plan["personnel"].id: plan for plan in plans}
    employee_numbers: dict[str, list[tuple[Personnel, bool]]] = {}
    for item in all_personnel:
        plan = plan_by_id.get(item.id)
        employee_no = plan["values"]["employee_no"] if plan else item.employee_no
        employee_numbers.setdefault(employee_no.casefold(), []).append((item, plan is not None))
    for duplicates in employee_numbers.values():
        if len(duplicates) > 1:
            for item, is_patched in duplicates:
                if is_patched:
                    errors.append(
                        _error(item.uuid, "employee_no", "Employee number already exists in this school")
                    )
    if errors:
        return _error_response(errors)

    changed_rows: list[Personnel] = []
    try:
        for plan in plans:
            personnel = plan["personnel"]
            before = {field: getattr(personnel, field) for field in EDITABLE_SYSTEM_FIELDS}
            for field, value in plan["values"].items():
                setattr(personnel, field, value)

            validated = plan["custom"] or []
            normalized = {str(definition.uuid): value for definition, value in validated}
            existing = {
                str(item.field_definition.uuid): item
                for item in personnel.custom_field_values
                if item.field_definition.is_active
            }
            for field_uuid in plan["patch"].custom_fields:
                value = normalized[field_uuid]
                current = existing.get(field_uuid)
                if current is None:
                    personnel.custom_field_values.append(
                        PersonnelCustomFieldValue(
                            field_definition=definition_by_uuid[field_uuid],
                            value=value,
                        )
                    )
                else:
                    current.value = value

            changes = {
                field: (old, getattr(personnel, field))
                for field, old in before.items()
                if old != getattr(personnel, field)
            }
            after_custom = dict(plan["audit_custom"])
            after_custom.update(
                {
                    definition_by_uuid[key].field_key: normalized[key]
                    for key in plan["patch"].custom_fields
                }
            )
            changes.update(custom_field_change_set(plan["audit_custom"], after_custom))
            if changes:
                personnel.updated_at = datetime.now(timezone.utc)
                record_personnel_field_changes(
                    db,
                    personnel=personnel,
                    actor=current_user,
                    changes=changes,
                )
                changed_rows.append(personnel)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _error_response(
            [{"personnel_uuid": "", "field": "grid", "message": "Personnel data changed while saving; refresh and try again"}],
            conflict=True,
        )
    except Exception:
        db.rollback()
        raise

    refreshed = (
        db.execute(
            select(Personnel)
            .options(*_options())
            .where(Personnel.id.in_([item.id for item in changed_rows]))
        ).scalars().all()
        if changed_rows
        else []
    )
    refreshed_by_uuid = {item.uuid: item for item in refreshed}
    ordered = [
        refreshed_by_uuid[item.personnel_uuid]
        for item in payload.rows
        if item.personnel_uuid in refreshed_by_uuid
    ]
    return PersonnelGridPatchResponse(
        updated_count=len(ordered),
        rows=[_row(item) for item in ordered],
    )
