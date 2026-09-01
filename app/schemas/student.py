from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StudentCustomFieldInput(BaseModel):
    field_uuid: UUID
    value: str


class StudentCustomFieldResponse(BaseModel):
    field_uuid: UUID
    field_key: str
    label: str
    data_type: str
    value: str
    is_active: bool

class BloodGroup(str, Enum):
    A_POSITIVE = "A+"
    A_NEGATIVE = "A-"
    B_POSITIVE = "B+"
    B_NEGATIVE = "B-"
    AB_POSITIVE = "AB+"
    AB_NEGATIVE = "AB-"
    O_POSITIVE = "O+"
    O_NEGATIVE = "O-"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    NEEDS_CORRECTION = "needs_correction"
    VERIFIED = "verified"


class StudentVerificationUpdate(BaseModel):
    status: VerificationStatus
    note: str | None = Field(default=None, max_length=2000)


class StudentBatchRequest(BaseModel):
    student_uuids: list[UUID] = Field(min_length=1, max_length=500)


class StudentBatchResult(BaseModel):
    updated_count: int
    students: list["StudentResponse"]


class StudentAuditEventResponse(BaseModel):
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

class StudentCreate(BaseModel):
    # ------------------------------------------------------
    # Academic Information
    # ------------------------------------------------------

    session_uuid: UUID
    class_uuid: UUID
    section_uuid: UUID

    admission_no: str
    roll_no: str | None = None
    stream: str | None = None

    # ------------------------------------------------------
    # Personal Information
    # ------------------------------------------------------

    full_name: str
    father_name: str | None = None
    mother_name: str | None = None
    dob: date | None = None
    gender: str | None = None
    blood_group: BloodGroup | None = None

    

    # ------------------------------------------------------
    # Contact Information
    # ------------------------------------------------------

    mobile: str | None = None
    aadhaar: str | None = None
    address: str | None = None

    # ------------------------------------------------------
    # Photo
    # ------------------------------------------------------

    photo_path: str | None = None
    custom_fields: list[StudentCustomFieldInput] = Field(default_factory=list)

class StudentUpdate(BaseModel):
    admission_no: str | None = None
    roll_no: str | None = None
    stream: str | None = None

    session_uuid: UUID | None = None
    class_uuid: UUID | None = None
    section_uuid: UUID | None = None

    full_name: str | None = None
    father_name: str | None = None
    mother_name: str | None = None
    dob: date | None = None
    gender: str | None = None
    blood_group: BloodGroup | None = None

    mobile: str | None = None
    aadhaar: str | None = None
    address: str | None = None
    photo_path: str | None = None

    is_active: bool | None = None
    custom_fields: list[StudentCustomFieldInput] | None = None


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    
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

    photo_path: str | None

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
    custom_fields: list[StudentCustomFieldResponse] = Field(default_factory=list)

class StudentPageResponse(BaseModel):
    items: list[StudentResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
