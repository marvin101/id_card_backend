from uuid import UUID
from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    email: str | None = None
    mobile: str | None = None
    is_platform_admin: bool = False

class SchoolAccessCreate(BaseModel):
    role: str


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