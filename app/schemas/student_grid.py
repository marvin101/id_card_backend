from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.custom_field import StudentFieldDefinitionResponse


class StudentGridLookupItem(BaseModel):
    uuid: UUID
    name: str
    class_uuid: UUID | None = None


class StudentGridRow(BaseModel):
    uuid: UUID
    updated_at: datetime
    session_uuid: UUID
    class_uuid: UUID
    section_uuid: UUID
    admission_no: str
    roll_no: str | None
    stream: str | None
    full_name: str
    father_name: str | None
    mother_name: str | None
    dob: date | None
    gender: str | None
    blood_group: str | None
    mobile: str | None
    aadhaar: str | None
    address: str | None
    custom_fields: dict[str, str] = Field(default_factory=dict)


class StudentGridResponse(BaseModel):
    rows: list[StudentGridRow]
    total: int
    offset: int
    limit: int
    has_more: bool
    custom_fields: list[StudentFieldDefinitionResponse]
    sessions: list[StudentGridLookupItem]
    classes: list[StudentGridLookupItem]
    sections: list[StudentGridLookupItem]


class StudentGridRowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_uuid: UUID
    expected_updated_at: datetime | None = None
    system_fields: dict[str, Any] = Field(default_factory=dict)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class StudentGridPatchRequest(BaseModel):
    rows: list[StudentGridRowPatch] = Field(min_length=1, max_length=200)


class StudentGridPatchResponse(BaseModel):
    updated_count: int
    rows: list[StudentGridRow]
