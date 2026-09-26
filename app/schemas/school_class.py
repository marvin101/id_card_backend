from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SchoolClassCreate(BaseModel):
    name: str


class SchoolClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    name: str
    sort_order: int

class SchoolClassUpdate(BaseModel):
    name: str | None = None


class SchoolClassReorder(BaseModel):
    class_uuids: list[UUID]
