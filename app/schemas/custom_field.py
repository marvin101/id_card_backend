from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomFieldDataType(str, Enum):
    text = "text"
    multiline = "multiline"
    number = "number"
    date = "date"
    phone = "phone"


class StudentFieldDefinitionCreate(BaseModel):
    field_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    data_type: CustomFieldDataType
    is_required: bool = False
    display_order: int | None = Field(default=None, ge=0)

    @field_validator("field_key", "label")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be blank")
        return stripped


class StudentFieldDefinitionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    data_type: CustomFieldDataType | None = None
    is_required: bool | None = None
    display_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("label cannot be blank")
        return stripped


class StudentFieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    field_key: str
    label: str
    data_type: str
    is_required: bool
    display_order: int
    is_active: bool


class StudentFieldReorderItem(BaseModel):
    field_uuid: UUID
    display_order: int = Field(ge=0)


class StudentFieldReorderRequest(BaseModel):
    fields: list[StudentFieldReorderItem]
