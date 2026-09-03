from datetime import datetime
import math
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


SUPPORTED_ELEMENT_TYPES = {
    "text",
    "bound_text",
    "custom_field_text",
    "student_photo",
    "school_logo",
    "rectangle",
    "line",
}
SUPPORTED_BINDING_FIELDS = {
    "full_name", "admission_no", "roll_no", "stream", "father_name",
    "mother_name", "dob", "gender", "blood_group", "mobile", "aadhaar",
    "address", "session", "class", "section",
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def validate_design_document(design: dict[str, Any]) -> dict[str, Any]:
    """Validate stored v1 settings or the bounded Designer v2 document."""
    version = design.get("schema_version", design.get("version", 1))
    if version == 1:
        return design
    if version != 2:
        raise ValueError("Unsupported card-template schema_version")

    canvas = design.get("canvas")
    if not isinstance(canvas, dict):
        raise ValueError("canvas must be an object")
    width = _finite_number(canvas.get("width"), "canvas.width")
    height = _finite_number(canvas.get("height"), "canvas.height")
    if width <= 0 or height <= 0 or width > 2000 or height > 2000:
        raise ValueError("canvas dimensions must be positive and at most 2000")
    if canvas.get("orientation") not in {"portrait", "landscape"}:
        raise ValueError("canvas.orientation must be portrait or landscape")
    background = canvas.get("background_color", "#FFFFFF")
    if not isinstance(background, str) or not _COLOR.fullmatch(background):
        raise ValueError("canvas.background_color must be a hex color")

    elements = design.get("elements")
    if not isinstance(elements, list) or len(elements) > 250:
        raise ValueError("elements must be a list containing at most 250 items")
    identifiers: set[str] = set()
    for index, element in enumerate(elements):
        prefix = f"elements[{index}]"
        if not isinstance(element, dict):
            raise ValueError(f"{prefix} must be an object")
        identifier = element.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 80:
            raise ValueError(f"{prefix}.id must be a non-empty string")
        if identifier in identifiers:
            raise ValueError("element IDs must be unique")
        identifiers.add(identifier)
        if element.get("type") not in SUPPORTED_ELEMENT_TYPES:
            raise ValueError(f"{prefix}.type is unsupported")
        for key in ("x", "y", "width", "height", "rotation", "z_index"):
            value = _finite_number(element.get(key), f"{prefix}.{key}")
            if key in {"width", "height"} and value <= 0:
                raise ValueError(f"{prefix}.{key} must be positive")
        if abs(float(element["z_index"])) > 10000:
            raise ValueError(f"{prefix}.z_index is outside the supported range")
        if not isinstance(element.get("locked"), bool) or not isinstance(element.get("visible"), bool):
            raise ValueError(f"{prefix}.locked and visible must be booleans")
        style = element.get("style", {})
        data = element.get("data", {})
        if not isinstance(style, dict) or not isinstance(data, dict):
            raise ValueError(f"{prefix}.style and data must be objects")
        for key in ("color", "fill_color", "border_color"):
            color = style.get(key)
            if color is not None and (not isinstance(color, str) or not _COLOR.fullmatch(color)):
                raise ValueError(f"{prefix}.style.{key} must be a hex color")
        for key, value in data.items():
            if isinstance(value, str) and len(value) > 2000:
                raise ValueError(f"{prefix}.data.{key} is too long")
        if element["type"] == "bound_text" and data.get("field") not in SUPPORTED_BINDING_FIELDS:
            raise ValueError(f"{prefix} has an unknown student field binding")
        if element["type"] == "custom_field_text":
            field_uuid = data.get("field_uuid")
            try:
                UUID(str(field_uuid))
            except (TypeError, ValueError):
                raise ValueError(f"{prefix}.data.field_uuid must be a UUID") from None
    settings = design.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    return design


class CardTemplateUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    design: dict[str, Any]

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value

    @field_validator("design")
    @classmethod
    def validate_design(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_design_document(value)


class CardTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    name: str
    design: dict[str, Any]
    updated_at: datetime
