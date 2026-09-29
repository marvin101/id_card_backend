from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.student_field_config import BUILTIN_STUDENT_FIELDS


class BuiltinStudentFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    data_type: str
    enabled: bool
    required: bool
    protected: bool
    display_order: int


class StreamOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=1, max_length=20)


class BuiltinStudentFieldConfigResponse(BaseModel):
    fields: list[BuiltinStudentFieldResponse]
    auto_admission_format: bool = False
    stream_options: list[StreamOption] = Field(default_factory=list)


class BuiltinStudentFieldWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    enabled: bool
    required: bool
    display_order: int = Field(ge=0)


class BuiltinStudentFieldConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[BuiltinStudentFieldWrite] = Field(min_length=1, max_length=len(BUILTIN_STUDENT_FIELDS))
    auto_admission_format: bool | None = None
    stream_options: list[StreamOption] | None = None

    @model_validator(mode="after")
    def validate_fields(self):
        keys = [item.key for item in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Built-in field keys must not be duplicated")
        unknown = sorted(set(keys) - set(BUILTIN_STUDENT_FIELDS))
        if unknown:
            raise ValueError(f"Unsupported built-in student field: {unknown[0]}")
        missing = sorted(set(BUILTIN_STUDENT_FIELDS) - set(keys))
        if missing:
            raise ValueError(f"Built-in student field is missing: {missing[0]}")
        orders = [item.display_order for item in self.fields]
        if len(orders) != len(set(orders)):
            raise ValueError("Display order values must not be duplicated")
        for item in self.fields:
            definition = BUILTIN_STUDENT_FIELDS[item.key]
            if definition.protected and (not item.enabled or not item.required):
                raise ValueError(f"Required by CampusID: {item.key}")
            if item.required and not item.enabled:
                raise ValueError(f"Required field must be enabled: {item.key}")
        return self
