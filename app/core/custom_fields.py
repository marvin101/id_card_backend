import re
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.custom_field import (
    CustomFieldDefinition,
    PersonnelCustomFieldValue,
    StudentCustomFieldValue,
)
from app.models.personnel import Personnel
from app.models.student import Student
from app.schemas.student import StudentCustomFieldInput


PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ().-]{4,24}$")


def validate_student_custom_fields(
    db: Session,
    school_id: int,
    submitted: list[StudentCustomFieldInput],
    *,
    require_all: bool,
    definitions: list[CustomFieldDefinition] | None = None,
) -> list[tuple[CustomFieldDefinition, str]]:
    uuids = [item.field_uuid for item in submitted]
    if len(uuids) != len(set(uuids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Duplicate custom field UUID submitted",
        )

    scoped_definitions = (
        [item for item in definitions if item.school_id == school_id
         and item.entity_type == "student" and item.uuid in uuids]
        if definitions is not None else db.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.uuid.in_(uuids), CustomFieldDefinition.school_id == school_id,
                CustomFieldDefinition.entity_type == "student",
            )
        ).scalars().all() if uuids else []
    )
    by_uuid = {definition.uuid: definition for definition in scoped_definitions}

    if len(by_uuid) != len(uuids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown custom field or field does not belong to this school",
        )

    inactive = [definition.label for definition in scoped_definitions if not definition.is_active]
    if inactive:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Inactive custom field cannot be submitted: {inactive[0]}",
        )

    if require_all:
        required = (
            [item for item in definitions if item.school_id == school_id and item.entity_type == "student"
             and item.is_active and item.is_required]
            if definitions is not None else db.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.school_id == school_id, CustomFieldDefinition.entity_type == "student",
                    CustomFieldDefinition.is_active.is_(True), CustomFieldDefinition.is_required.is_(True),
                )
            ).scalars().all()
        )
        missing = [item.label for item in required if item.uuid not in by_uuid]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Required custom field is missing: {missing[0]}",
            )

    validated: list[tuple[CustomFieldDefinition, str]] = []
    for item in submitted:
        definition = by_uuid[item.field_uuid]
        value = item.value.strip()
        if definition.is_required and not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} is required",
            )
        if value:
            value = _normalize_value(definition, value)
        validated.append((definition, value))
    return validated


def validate_personnel_custom_fields(
    db: Session,
    school_id: int,
    personnel_type: str,
    submitted: list[StudentCustomFieldInput],
    *,
    require_all: bool,
    definitions: list[CustomFieldDefinition] | None = None,
) -> list[tuple[CustomFieldDefinition, str]]:
    """Validate personnel values against the definitions for their exact type."""
    uuids = [item.field_uuid for item in submitted]
    if len(uuids) != len(set(uuids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Duplicate custom field UUID submitted",
        )
    scoped_definitions = (
        [
            definition
            for definition in definitions
            if definition.school_id == school_id
            and definition.entity_type == personnel_type
            and definition.uuid in uuids
        ]
        if definitions is not None
        else db.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.uuid.in_(uuids),
                CustomFieldDefinition.school_id == school_id,
                CustomFieldDefinition.entity_type == personnel_type,
            )
        ).scalars().all()
        if uuids
        else []
    )
    by_uuid = {definition.uuid: definition for definition in scoped_definitions}
    if len(by_uuid) != len(uuids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown custom field or field does not belong to this school and personnel type",
        )
    inactive = [item.label for item in scoped_definitions if not item.is_active]
    if inactive:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Inactive custom field cannot be submitted: {inactive[0]}",
        )
    if require_all:
        required = (
            [
                definition
                for definition in definitions
                if definition.school_id == school_id
                and definition.entity_type == personnel_type
                and definition.is_active
                and definition.is_required
            ]
            if definitions is not None
            else db.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.school_id == school_id,
                    CustomFieldDefinition.entity_type == personnel_type,
                    CustomFieldDefinition.is_active.is_(True),
                    CustomFieldDefinition.is_required.is_(True),
                )
            ).scalars().all()
        )
        missing = [item.label for item in required if item.uuid not in by_uuid]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Required custom field is missing: {missing[0]}",
            )
    validated = []
    for item in submitted:
        definition = by_uuid[item.field_uuid]
        value = item.value.strip()
        if definition.is_required and not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} is required",
            )
        validated.append((definition, _normalize_value(definition, value) if value else value))
    return validated


def _normalize_value(definition: CustomFieldDefinition, value: str) -> str:
    if definition.data_type == "number":
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} must be a number",
            ) from exc
        if not number.is_finite():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} must be a finite number",
            )
        return format(number.normalize(), "f")
    if definition.data_type == "date":
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} must be a valid YYYY-MM-DD date",
            ) from exc
    if definition.data_type == "phone":
        if not PHONE_PATTERN.fullmatch(value) or sum(char.isdigit() for char in value) < 5:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{definition.label} must be a valid phone number",
            )
    return value


def replace_student_custom_fields(
    db: Session,
    student: Student,
    validated: list[tuple[CustomFieldDefinition, str]],
) -> None:
    existing = {
        item.field_definition_id or item.field_definition.id: item
        for item in student.custom_field_values
    }
    submitted_ids = {definition.id for definition, _ in validated}
    for item in list(student.custom_field_values):
        effective_definition_id = (
            item.field_definition_id or item.field_definition.id
        )
        if (
            effective_definition_id not in submitted_ids
            and item.field_definition.is_active
        ):
            db.delete(item)
    for definition, value in validated:
        current = existing.get(definition.id)
        if current is None:
            student.custom_field_values.append(
                StudentCustomFieldValue(field_definition=definition, value=value)
            )
        else:
            current.value = value


def replace_personnel_custom_fields(
    db: Session,
    personnel: Personnel,
    validated: list[tuple[CustomFieldDefinition, str]],
) -> None:
    existing = {
        item.field_definition_id or item.field_definition.id: item
        for item in personnel.custom_field_values
    }
    submitted_ids = {definition.id for definition, _ in validated}
    for item in list(personnel.custom_field_values):
        definition_id = item.field_definition_id or item.field_definition.id
        if definition_id not in submitted_ids and item.field_definition.is_active:
            db.delete(item)
    for definition, value in validated:
        current = existing.get(definition.id)
        if current is None:
            personnel.custom_field_values.append(
                PersonnelCustomFieldValue(field_definition=definition, value=value)
            )
        else:
            current.value = value
