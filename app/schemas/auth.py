from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from typing import Literal

SchoolRole = Literal["school_admin", "card_operator", "teacher", "staff"]

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


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str

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
