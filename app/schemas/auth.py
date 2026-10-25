from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Literal

SchoolRole = Literal["school_admin", "card_operator", "teacher", "staff"]


def _normalize_optional_text(value):
    if isinstance(value, str):
        return value.strip() or None
    return value


def _validate_optional_email(value):
    if value:
        local, separator, domain = value.rpartition("@")
        if (
            not separator
            or not local
            or "." not in domain
            or any(c.isspace() for c in value)
        ):
            raise ValueError("Enter a valid email address")
    return value


class SchoolAccessCreate(BaseModel):
    role: SchoolRole


class UserResponse(BaseModel):
    uuid: UUID
    username: str
    full_name: str
    email: str | None = None
    mobile: str | None = None
    designation: str | None = None
    platform_role: str | None = None
    is_platform_admin: bool
    is_active: bool


class SelfProfileSchool(BaseModel):
    school_uuid: UUID
    school_name: str
    role: str


class SelfProfileResponse(UserResponse):
    profile_photo_url: str | None = None
    last_login: datetime | None = None
    created_at: datetime
    updated_at: datetime
    school_contexts: list[SelfProfileSchool] = Field(default_factory=list)


class SelfProfileUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    username: str | None = Field(default=None, min_length=3, max_length=100, pattern=r"^\S+$")
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=150)
    mobile: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_nonnullable_name(self):
        for name in ("username", "full_name"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    @field_validator("username", "full_name", "email", "mobile", mode="before")
    @classmethod
    def normalize_profile_fields(cls, value):
        return _normalize_optional_text(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        return _validate_optional_email(value)


class ChangePasswordRequest(BaseModel):
    model_config = {"extra": "forbid"}

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
    confirm_new_password: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def validate_password_confirmation(self):
        if self.new_password != self.confirm_new_password:
            raise ValueError("New passwords do not match.")
        return self


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    refresh_token: str | None = None
    expires_in: int | None = None
    refresh_expires_in: int | None = None

class SchoolAccessResponse(BaseModel):
    user_uuid: UUID
    school_uuid: UUID
    school_name: str
    # A string keeps old ``admin`` rows readable until they are deliberately
    # changed to ``school_admin`` by an administrator.
    role: str


class SchoolUserAssignmentResponse(BaseModel):
    """A user and their assignment state for one selected school."""

    user_uuid: UUID
    username: str
    full_name: str
    email: str | None = None
    mobile: str | None = None
    designation: str | None = None
    role: str | None = None
    assignment_status: Literal["assigned", "pending_assignment"]


class SchoolAccessUpdate(BaseModel):
    role: SchoolRole


class RegistrationSchoolResponse(BaseModel):
    uuid: UUID
    school_name: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=1, max_length=150)
    email: str | None = None
    mobile: str | None = None
    designation: str = Field(min_length=1, max_length=100)
    school_uuid: UUID | None = None
    # Temporary compatibility for older deployed clients. New clients submit
    # school_uuid from the registration-school selector.
    school_name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_school_selection(self):
        if self.school_uuid is None and self.school_name is None:
            raise ValueError("Select a school.")
        return self


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class AdminUserCreate(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=3, max_length=100, pattern=r"^\S+$")
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=150)
    mobile: str | None = Field(default=None, max_length=20)
    designation: str | None = Field(default=None, max_length=100)
    platform_role: Literal["platform_admin"] | None = None

    @field_validator("full_name", "email", "mobile", "designation", mode="before")
    @classmethod
    def normalize_contact(cls, value):
        return _normalize_optional_text(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        return _validate_optional_email(value)


class AdminUserUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=150)
    mobile: str | None = Field(default=None, max_length=20)
    designation: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)
    platform_role: Literal["platform_admin"] | None = None

    @model_validator(mode="after")
    def validate_nonnullable(self):
        for name in ("full_name", "is_active", "password"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if self.full_name is not None and not self.full_name.strip():
            raise ValueError("Full name cannot be empty")
        return self

    @field_validator("full_name", "email", "mobile", "designation", mode="before")
    @classmethod
    def normalize_contact(cls, value):
        return _normalize_optional_text(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        return _validate_optional_email(value)
    
class SecurityEventResponse(BaseModel):
    uuid: UUID
    event_type: str
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict | None = None
    created_at: datetime


class SecurityEventListResponse(BaseModel):
    items: list[SecurityEventResponse]
    total: int
    limit: int
    offset: int

class AuthSessionResponse(BaseModel):
    session_id: UUID
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class AuthSessionListResponse(BaseModel):
    items: list[AuthSessionResponse]


class MfaEnrollmentStartResponse(BaseModel):
    provisioning_uri: str
    message: str


class MfaEnrollmentVerifyRequest(BaseModel):
    code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
    )


class MfaRecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]
    message: str

class MfaChallengeResponse(BaseModel):
    mfa_required: bool = True
    challenge_token: str


class MfaChallengeVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=1, max_length=4096)
    code: str = Field(min_length=6, max_length=64)

class MfaStatusResponse(BaseModel):
    enabled: bool
    recovery_codes_remaining: int
    recovery_codes_can_regenerate: bool = False


class MfaDisableRequest(BaseModel):
    model_config = {"extra": "forbid"}

    current_password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=64)
