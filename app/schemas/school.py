from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _empty_to_none(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class SchoolCreate(BaseModel):
    school_code: str = Field(min_length=1, max_length=30)
    school_name: str = Field(min_length=1, max_length=200)

    email: str | None = None
    phone: str | None = None
    website: str | None = None

    address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    country: str | None = "India"
    postal_code: str | None = None

    principal_name: str | None = None


class SchoolUpdate(BaseModel):
    school_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    website: str | None = Field(default=None, max_length=200)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    principal_name: str | None = Field(default=None, max_length=150)

    @field_validator(
        "email",
        "phone",
        "website",
        "address",
        "city",
        "district",
        "state",
        "country",
        "postal_code",
        "principal_name",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return _empty_to_none(value)

    @field_validator("school_name", mode="before")
    @classmethod
    def normalize_school_name(cls, value: object) -> object:
        if value is None:
            raise ValueError("School name cannot be cleared.")
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None:
            local, separator, domain = value.rpartition("@")
            if not separator or not local or "." not in domain:
                raise ValueError("Enter a valid email address.")
        return value

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Website must be a complete HTTP or HTTPS URL.")
        return value


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
    logo_url: str | None = None
    principal_name: str | None

    is_active: bool
