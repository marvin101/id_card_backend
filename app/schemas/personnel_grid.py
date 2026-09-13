from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.custom_field import StudentFieldDefinitionResponse
from app.schemas.personnel import PersonnelType


class PersonnelGridRow(BaseModel):
    uuid: UUID
    updated_at: datetime
    personnel_type: PersonnelType
    employee_no: str
    full_name: str
    designation: str | None
    department: str | None
    dob: date | None
    gender: str | None
    blood_group: str | None
    mobile: str | None
    email: str | None
    address: str | None
    is_active: bool
    custom_fields: dict[str, str] = Field(default_factory=dict)


class PersonnelGridResponse(BaseModel):
    rows: list[PersonnelGridRow]
    total: int
    offset: int
    limit: int
    has_more: bool
    personnel_type: PersonnelType
    custom_fields: list[StudentFieldDefinitionResponse]
    departments: list[str]
    designations: list[str]


class PersonnelGridRowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    personnel_uuid: UUID
    expected_updated_at: datetime | None = None
    system_fields: dict[str, Any] = Field(default_factory=dict)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class PersonnelGridPatchRequest(BaseModel):
    rows: list[PersonnelGridRowPatch] = Field(min_length=1, max_length=200)


class PersonnelGridPatchResponse(BaseModel):
    updated_count: int
    rows: list[PersonnelGridRow]
