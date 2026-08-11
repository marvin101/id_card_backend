from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SectionCreate(BaseModel):
    name: str


class SectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    name: str

class SectionUpdate(BaseModel):
    name: str | None = None