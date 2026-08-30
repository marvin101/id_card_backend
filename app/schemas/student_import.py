from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class StudentImportMappingItem(BaseModel):
    source_column: str
    target_field: str


class StudentImportMapping(BaseModel):
    mappings: list[StudentImportMappingItem]

    @model_validator(mode="after")
    def mappings_are_one_to_one(self):
        sources = [item.source_column for item in self.mappings]
        targets = [item.target_field for item in self.mappings]
        if len(sources) != len(set(sources)):
            raise ValueError("A spreadsheet column can only be mapped once")
        if len(targets) != len(set(targets)):
            raise ValueError("A target field can only be mapped once")
        return self


class StudentImportCommitRequest(StudentImportMapping):
    confirmed: bool


class StudentImportField(BaseModel):
    key: str
    label: str
    required: bool = False
    data_type: str = "text"
    custom_field_uuid: UUID | None = None


class StudentImportUploadResponse(BaseModel):
    upload_id: UUID
    filename: str
    headers: list[str]
    row_count: int
    target_fields: list[StudentImportField]
    suggested_mappings: list[StudentImportMappingItem]


class StudentImportRowPreview(BaseModel):
    row_number: int
    values: dict[str, Any]
    errors: list[str] = Field(default_factory=list)


class StudentImportPreviewResponse(BaseModel):
    upload_id: UUID
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    can_import: bool
    rows: list[StudentImportRowPreview]


class StudentImportSummary(BaseModel):
    upload_id: UUID
    imported_count: int
    skipped_count: int = 0
    message: str
