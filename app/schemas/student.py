from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
    blood_group: str | None = None

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
    blood_group: str | None = None

    mobile: str | None = None
    aadhaar: str | None = None
    address: str | None = None
    photo_path: str | None = None

    is_active: bool | None = None


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID

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

    is_active: bool