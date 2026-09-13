from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.personnel import PersonnelType


class PersonnelImportMappingItem(BaseModel):
    source_column: str
    target_field: str


class PersonnelImportMapping(BaseModel):
    mappings: list[PersonnelImportMappingItem]

    @model_validator(mode="after")
    def mappings_are_one_to_one(self):
        sources = [item.source_column for item in self.mappings]
        targets = [item.target_field for item in self.mappings]
        if len(sources) != len(set(sources)):
            raise ValueError("A spreadsheet column can only be mapped once")
        if len(targets) != len(set(targets)):
            raise ValueError("A target field can only be mapped once")
        return self


class PersonnelImportCommitRequest(PersonnelImportMapping):
    confirmed: bool


class PersonnelImportField(BaseModel):
    key: str
    label: str
    required: bool = False
    data_type: str = "text"
    custom_field_uuid: UUID | None = None


class PersonnelImportUploadResponse(BaseModel):
    upload_id: UUID
    filename: str
    personnel_type: PersonnelType
    headers: list[str]
    row_count: int
    target_fields: list[PersonnelImportField]
    suggested_mappings: list[PersonnelImportMappingItem]


class PersonnelImportRowPreview(BaseModel):
    row_number: int
    values: dict[str, Any]
    errors: list[str] = Field(default_factory=list)


class PersonnelImportPreviewResponse(BaseModel):
    upload_id: UUID
    personnel_type: PersonnelType
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    can_import: bool
    rows: list[PersonnelImportRowPreview]


class PersonnelImportSummary(BaseModel):
    upload_id: UUID
    personnel_type: PersonnelType
    imported_count: int
    skipped_count: int = 0
    message: str
