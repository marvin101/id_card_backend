from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SchoolCreate(BaseModel):
    school_code: str
    school_name: str

    email: str | None = None
    phone: str | None = None
    website: str | None = None

    address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    country: str | None = "India"
    postal_code: str | None = None

    logo_path: str | None = None
    principal_name: str | None = None


class SchoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    school_code: str
    school_name: str

    email: str | None
    phone: str | None
    website: str | None

    address: str | None
    city: str | None
    district: str | None
    state: str | None
    country: str | None
    postal_code: str | None

    logo_path: str | None
    principal_name: str | None

    is_active: bool