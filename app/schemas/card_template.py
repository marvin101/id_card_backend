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
    "full_name",
    "admission_no",
    "roll_no",
    "stream",
    "father_name",
    "mother_name",
    "dob",
    "gender",
    "blood_group",
    "mobile",
    "aadhaar",
    "address",
    "session",
    "class",
    "section",
    "school_name",
    "school_address",
    "school_code",
    "school_phone",
    "school_email",
    "school_website",
    "school_city",
    "school_district",
    "school_state",
    "school_country",
    "school_postal_code",
    "principal_name",
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_TEXT_DATA_KEYS = {"text", "prefix", "suffix", "fallback", "label"}


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _optional_finite_number(
    values: dict[str, Any], key: str, label: str, *, minimum: float | None = None
) -> float | None:
    if key not in values:
        return None
    number = _finite_number(values[key], label)
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}")
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
    if width <= 10 or height <= 10 or width > 2000 or height > 2000:
        raise ValueError("canvas dimensions must be greater than 10 and at most 2000 millimetres")
    if canvas.get("orientation") not in {"portrait", "landscape"}:
        raise ValueError("canvas.orientation must be portrait or landscape")
    expected_orientation = "landscape" if width >= height else "portrait"
    if canvas["orientation"] != expected_orientation:
        raise ValueError(
            f"canvas.orientation must be {expected_orientation} for these dimensions"
        )
    background = canvas.get("background_color", "#FFFFFF")
    if not isinstance(background, str) or not _COLOR.fullmatch(background):
        raise ValueError("canvas.background_color must be a hex color")
    background_image = canvas.get("background_image")
    if background_image is not None and not isinstance(background_image, str):
        raise ValueError("canvas.background_image must be a string or null")

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
            if key in {"x", "y"} and not 0 <= value <= 2000:
                raise ValueError(f"{prefix}.{key} is outside the supported range")
            if key in {"width", "height"} and value > 2000:
                raise ValueError(f"{prefix}.{key} is outside the supported range")
            if key == "rotation" and abs(value) > 360:
                raise ValueError(f"{prefix}.rotation is outside the supported range")
        if not float(element["z_index"]).is_integer():
            raise ValueError(f"{prefix}.z_index must be an integer")
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
        _optional_finite_number(
            style, "border_width", f"{prefix}.style.border_width", minimum=0
        )
        _optional_finite_number(
            style, "corner_radius", f"{prefix}.style.corner_radius", minimum=0
        )
        font_size = _optional_finite_number(
            style, "font_size", f"{prefix}.style.font_size", minimum=0
        )
        if font_size == 0:
            raise ValueError(f"{prefix}.style.font_size must be positive")
        if "font_weight" in style:
            font_weight = _finite_number(
                style["font_weight"], f"{prefix}.style.font_weight"
            )
            if (
                not font_weight.is_integer()
                or font_weight % 100
                or not 100 <= font_weight <= 900
            ):
                raise ValueError(
                    f"{prefix}.style.font_weight must be 100 through 900 "
                    "in steps of 100"
                )
        if "max_lines" in style:
            max_lines = _finite_number(
                style["max_lines"], f"{prefix}.style.max_lines"
            )
            if not max_lines.is_integer() or max_lines < 1:
                raise ValueError(
                    f"{prefix}.style.max_lines must be a positive integer"
                )
        if style.get("alignment") not in {None, "left", "center", "right"}:
            raise ValueError(f"{prefix}.style.alignment is unsupported")
        if style.get("fit") not in {None, "cover", "contain"}:
            raise ValueError(f"{prefix}.style.fit is unsupported")
        for key, value in data.items():
            if isinstance(value, str) and len(value) > 2000:
                raise ValueError(f"{prefix}.data.{key} is too long")
        for key in _TEXT_DATA_KEYS & data.keys():
            if not isinstance(data[key], str):
                raise ValueError(f"{prefix}.data.{key} must be a string")
        if (
            element["type"] == "bound_text"
            and data.get("field") not in SUPPORTED_BINDING_FIELDS
        ):
            raise ValueError(f"{prefix} has an unknown student field binding")
        if element["type"] == "custom_field_text":
            field_uuid = data.get("field_uuid")
            try:
                parsed_uuid = UUID(field_uuid) if isinstance(field_uuid, str) else None
            except (TypeError, ValueError):
                raise ValueError(f"{prefix}.data.field_uuid must be a UUID") from None
            if parsed_uuid is None or str(parsed_uuid) != field_uuid:
                raise ValueError(
                    f"{prefix}.data.field_uuid must be a canonical UUID"
                )
    settings = design.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    for key in ("grid_enabled", "snap_enabled"):
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f"settings.{key} must be a boolean")
    grid_size = _optional_finite_number(
        settings, "grid_size", "settings.grid_size", minimum=0
    )
    if grid_size == 0 or (grid_size is not None and grid_size > 200):
        raise ValueError(
            "settings.grid_size must be greater than 0 and at most 200"
        )
    return design


class CardTemplateUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    design: dict[str, Any]

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("name cannot be blank")
        return trimmed

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
