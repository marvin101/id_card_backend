from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.student import BloodGroup, VerificationStatus


class PersonnelType(str, Enum):
    TEACHER = "teacher"
    STAFF = "staff"


class _PersonnelData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    personnel_type: PersonnelType
    employee_no: str = Field(min_length=1, max_length=50)
    full_name: str = Field(min_length=1, max_length=150)
    designation: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    dob: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    blood_group: BloodGroup | None = None
    mobile: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "designation", "department", "gender", "mobile", "email", "address", mode="after"
    )
    @classmethod
    def empty_optional_strings_are_none(cls, value: str | None) -> str | None:
        return value or None


class PersonnelCreate(_PersonnelData):
    pass


class PersonnelUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    personnel_type: PersonnelType | None = None
    employee_no: str | None = Field(default=None, min_length=1, max_length=50)
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    designation: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    dob: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    blood_group: BloodGroup | None = None
    mobile: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "personnel_type", "employee_no", "full_name", mode="before"
    )
    @classmethod
    def required_fields_cannot_be_cleared(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be null")
        return value

    @field_validator(
        "designation", "department", "gender", "mobile", "email", "address", mode="after"
    )
    @classmethod
    def empty_optional_strings_are_none(cls, value: str | None) -> str | None:
        return value or None


class PersonnelVerificationUpdate(BaseModel):
    status: VerificationStatus
    note: str | None = Field(default=None, max_length=2000)


class PersonnelBatchRequest(BaseModel):
    personnel_uuids: list[UUID] = Field(min_length=1, max_length=500)


class PersonnelAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    event_type: str
    field_name: str | None
    old_value: object | None
    new_value: object | None
    note: str | None
    actor_user_uuid: UUID | None
    actor_name: str | None
    created_at: datetime


class PersonnelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
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
    photo_path: str | None
    linked_user_uuid: UUID | None

    verification_status: VerificationStatus = VerificationStatus.PENDING
    lifecycle_status: str = "pending"
    correction_note: str | None = None
    verified_at: datetime | None = None
    verified_by_user_uuid: UUID | None = None
    verified_by_name: str | None = None
    printed_at: datetime | None = None
    printed_by_user_uuid: UUID | None = None
    printed_by_name: str | None = None
    print_count: int = 0

    is_active: bool
    created_at: datetime
    updated_at: datetime
    custom_fields: list[dict] = Field(default_factory=list)


class PersonnelPageResponse(BaseModel):
    items: list[PersonnelResponse]
    total: int
    offset: int
    limit: int
    has_more: bool


class PersonnelBatchResult(BaseModel):
    updated_count: int
    personnel: list[PersonnelResponse]
