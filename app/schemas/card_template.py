from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CardTemplateUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    design: dict[str, Any]


class CardTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    name: str
    design: dict[str, Any]
    updated_at: datetime
