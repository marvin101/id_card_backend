from datetime import datetime

from pydantic import BaseModel, Field, field_validator


PUBLIC_VERIFICATION_FIELDS = {
    "full_name": "Full name",
    "admission_no": "Admission number",
    "roll_no": "Roll number",
    "stream": "Stream",
    "session": "Academic session",
    "class": "Class",
    "section": "Section",
    "photo": "Student photo",
}
DEFAULT_PUBLIC_VERIFICATION_FIELDS = [
    "full_name",
    "admission_no",
    "class",
    "section",
]


class PublicVerificationSettingsUpdate(BaseModel):
    enabled: bool
    fields: list[str] = Field(min_length=1, max_length=8)
    validity_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: list[str]) -> list[str]:
        if len(fields) != len(set(fields)):
            raise ValueError("Verification fields must be unique")
        unsupported = set(fields) - set(PUBLIC_VERIFICATION_FIELDS)
        if unsupported:
            raise ValueError(
                f"Unsupported public verification field: {sorted(unsupported)[0]}"
            )
        return fields


class PublicVerificationFieldOption(BaseModel):
    key: str
    label: str


class PublicVerificationSettingsResponse(BaseModel):
    enabled: bool
    fields: list[str]
    validity_days: int
    available_fields: list[PublicVerificationFieldOption]


class StudentVerificationLinkUpdate(BaseModel):
    enabled: bool


class StudentVerificationLinkResponse(BaseModel):
    enabled: bool
    verification_url: str | None
    credential_status: str
    credential_version: int
    issued_at: datetime
    expires_at: datetime


class PublicVerificationSchool(BaseModel):
    school_name: str
    school_code: str
    logo_url: str | None = None


class PublicVerificationValue(BaseModel):
    key: str
    label: str
    value: str


class PublicStudentVerificationView(BaseModel):
    school: PublicVerificationSchool
    verification_status: str
    lifecycle_status: str
    verified_at: datetime | None = None
    photo_url: str | None = None
    credential_status: str
    credential_version: int
    credential_issued_at: datetime
    credential_expires_at: datetime
    signature_verified: bool
    fields: list[PublicVerificationValue]
