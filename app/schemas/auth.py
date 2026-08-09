from uuid import UUID
from pydantic import BaseModel
from typing import Literal


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    email: str | None = None
    mobile: str | None = None
    is_platform_admin: bool = False

class SchoolAccessCreate(BaseModel):
    role: SchoolRole


class UserResponse(BaseModel):
    uuid: UUID
    username: str
    full_name: str
    email: str | None = None
    mobile: str | None = None
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
    role: SchoolRole

class SchoolAccessUpdate(BaseModel):
    role: SchoolRole

SchoolRole = Literal["admin", "teacher", "staff"]