from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.student import BloodGroup


SYSTEM_FIELD_KEYS = (
    "session_uuid", "class_uuid", "section_uuid", "admission_no", "roll_no",
    "stream", "full_name", "father_name", "mother_name", "dob", "gender",
    "blood_group", "mobile", "aadhaar", "address",
)
REQUIRED_SYSTEM_FIELD_KEYS = frozenset({
    "session_uuid", "class_uuid", "section_uuid", "admission_no", "full_name"
})


class PublicFormConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    instructions: str | None = Field(default=None, max_length=4000)
    is_active: bool = False
    require_all_fields: bool = False
    allow_photo: bool = False
    expires_at: datetime | None = None
    selected_system_fields: list[str] = Field(default_factory=list, max_length=len(SYSTEM_FIELD_KEYS))
    selected_custom_field_uuids: list[UUID] = Field(default_factory=list, max_length=100)
    success_message: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value

    @field_validator("instructions", "success_message")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("selected_system_fields")
    @classmethod
    def validate_system_fields(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("System fields must not be duplicated")
        unknown = set(value) - set(SYSTEM_FIELD_KEYS)
        if unknown:
            raise ValueError(f"Unsupported system field: {sorted(unknown)[0]}")
        missing = REQUIRED_SYSTEM_FIELD_KEYS - set(value)
        if missing:
            raise ValueError(f"Required system field must be selected: {sorted(missing)[0]}")
        return value

    @field_validator("selected_custom_field_uuids")
    @classmethod
    def unique_custom_fields(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Custom fields must not be duplicated")
        return value


class PublicFormConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    uuid: UUID
    public_token: str
    title: str
    instructions: str | None
    is_active: bool
    require_all_fields: bool
    allow_photo: bool
    expires_at: datetime | None
    selected_system_fields: list[str]
    selected_custom_field_uuids: list[UUID]
    success_message: str | None
    created_at: datetime
    updated_at: datetime


class PublicField(BaseModel):
    key: str
    label: str
    data_type: str
    required: bool
    kind: Literal["system", "custom"]
    field_uuid: UUID | None = None
    options: list[dict[str, str]] | None = None


class PublicFormView(BaseModel):
    school_name: str
    school_logo_url: str | None
    title: str
    instructions: str | None
    fields: list[PublicField]
    allow_photo: bool
    supported_photo_types: list[str]
    max_photo_size_bytes: int
    success_message: str | None


class PublicCustomFieldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_uuid: UUID
    value: str = Field(max_length=4000)


class PublicStudentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_uuid: UUID | None = None
    class_uuid: UUID | None = None
    section_uuid: UUID | None = None
    admission_no: str | None = Field(default=None, max_length=50)
    roll_no: str | None = Field(default=None, max_length=30)
    stream: str | None = Field(default=None, max_length=50)
    full_name: str | None = Field(default=None, max_length=150)
    father_name: str | None = Field(default=None, max_length=150)
    mother_name: str | None = Field(default=None, max_length=150)
    dob: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    blood_group: BloodGroup | None = None
    mobile: str | None = Field(default=None, max_length=20)
    aadhaar: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=2000)
    custom_fields: list[PublicCustomFieldInput] = Field(default_factory=list, max_length=100)

    @field_validator("admission_no", "roll_no", "stream", "full_name", "father_name", "mother_name", "gender", "mobile", "aadhaar", "address")
    @classmethod
    def strip_strings(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class PublicSubmissionResponse(BaseModel):
    submitted: bool = True
    message: str
