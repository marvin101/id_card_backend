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
    "qr_code",
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
SUPPORTED_QR_BINDING_FIELDS = SUPPORTED_BINDING_FIELDS | {"verification_url"}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_TEXT_DATA_KEYS = {"text", "prefix", "suffix", "fallback", "label"}
_KNOWN_STYLE_KEYS = {
    "color",
    "fill_color",
    "border_color",
    "border_width",
    "corner_radius",
    "font_size",
    "font_weight",
    "max_lines",
    "alignment",
    "fit",
    "background_color",
    "quiet_zone",
    "error_correction",
}
_STYLE_KEYS_BY_TYPE = {
    "text": {"color", "font_size", "font_weight", "max_lines", "alignment"},
    "bound_text": {
        "color",
        "font_size",
        "font_weight",
        "max_lines",
        "alignment",
    },
    "custom_field_text": {
        "color",
        "font_size",
        "font_weight",
        "max_lines",
        "alignment",
    },
    "student_photo": {"fit", "border_color", "border_width", "corner_radius"},
    "school_logo": {"fit", "border_color", "border_width", "corner_radius"},
    "rectangle": {"fill_color", "border_color", "border_width", "corner_radius"},
    "line": {"color", "border_width"},
    "qr_code": {"color", "background_color", "quiet_zone", "error_correction"},
}
_KNOWN_DATA_KEYS = _TEXT_DATA_KEYS | {"field", "field_uuid", "fields", "format"}
_DATA_KEYS_BY_TYPE = {
    "text": {"text", "prefix", "suffix"},
    "bound_text": {"field", "prefix", "suffix", "fallback", "label"},
    "custom_field_text": {
        "field_uuid",
        "prefix",
        "suffix",
        "fallback",
        "label",
    },
    "student_photo": set(),
    "school_logo": set(),
    "rectangle": set(),
    "line": set(),
    "qr_code": {
        "text",
        "field",
        "field_uuid",
        "fields",
        "format",
        "prefix",
        "suffix",
        "fallback",
        "label",
    },
}


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
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("card-template schema_version must be an integer")
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
    z_indices: set[int] = set()
    for index, element in enumerate(elements):
        prefix = f"elements[{index}]"
        if not isinstance(element, dict):
            raise ValueError(f"{prefix} must be an object")
        identifier = element.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier != identifier.strip()
            or len(identifier) > 80
        ):
            raise ValueError(f"{prefix}.id must be a non-empty string")
        if identifier in identifiers:
            raise ValueError("element IDs must be unique")
        identifiers.add(identifier)
        element_type = element.get("type")
        if element_type not in SUPPORTED_ELEMENT_TYPES:
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
        if element_type == "qr_code":
            qr_width = float(element["width"])
            qr_height = float(element["height"])
            if qr_width < 12 or qr_height < 12:
                raise ValueError(f"{prefix} QR dimensions must be at least 12 millimetres")
            if not math.isclose(qr_width, qr_height, abs_tol=0.01):
                raise ValueError(f"{prefix} QR dimensions must be square")
        if not float(element["z_index"]).is_integer():
            raise ValueError(f"{prefix}.z_index must be an integer")
        if abs(float(element["z_index"])) > 10000:
            raise ValueError(f"{prefix}.z_index is outside the supported range")
        z_index = int(element["z_index"])
        if z_index in z_indices:
            raise ValueError("element z_index values must be unique")
        z_indices.add(z_index)
        if not isinstance(element.get("locked"), bool) or not isinstance(element.get("visible"), bool):
            raise ValueError(f"{prefix}.locked and visible must be booleans")
        style = element.get("style", {})
        data = element.get("data", {})
        if not isinstance(style, dict) or not isinstance(data, dict):
            raise ValueError(f"{prefix}.style and data must be objects")
        unsupported_style = (
            _KNOWN_STYLE_KEYS & style.keys()
        ) - _STYLE_KEYS_BY_TYPE[element_type]
        if unsupported_style:
            key = sorted(unsupported_style)[0]
            raise ValueError(
                f"{prefix}.style.{key} is unsupported for {element_type}"
            )
        unsupported_data = (
            _KNOWN_DATA_KEYS & data.keys()
        ) - _DATA_KEYS_BY_TYPE[element_type]
        if unsupported_data:
            key = sorted(unsupported_data)[0]
            raise ValueError(
                f"{prefix}.data.{key} is unsupported for {element_type}"
            )
        for key in ("color", "fill_color", "border_color"):
            color = style.get(key)
            if color is not None and (not isinstance(color, str) or not _COLOR.fullmatch(color)):
                raise ValueError(f"{prefix}.style.{key} must be a hex color")
        border_width = _optional_finite_number(
            style, "border_width", f"{prefix}.style.border_width", minimum=0
        )
        if border_width is not None and border_width > 10:
            raise ValueError(f"{prefix}.style.border_width must be at most 10")
        if element_type == "line" and border_width == 0:
            raise ValueError(f"{prefix}.style.border_width must be positive for line")
        corner_radius = _optional_finite_number(
            style, "corner_radius", f"{prefix}.style.corner_radius", minimum=0
        )
        if corner_radius is not None and corner_radius > 30:
            raise ValueError(f"{prefix}.style.corner_radius must be at most 30")
        font_size = _optional_finite_number(
            style, "font_size", f"{prefix}.style.font_size", minimum=0
        )
        if font_size == 0:
            raise ValueError(f"{prefix}.style.font_size must be positive")
        if font_size is not None and font_size > 20:
            raise ValueError(f"{prefix}.style.font_size must be at most 20")
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
            if not max_lines.is_integer() or not 1 <= max_lines <= 100:
                raise ValueError(
                    f"{prefix}.style.max_lines must be a positive integer "
                    "no greater than 100"
                )
        if style.get("alignment") not in {None, "left", "center", "right"}:
            raise ValueError(f"{prefix}.style.alignment is unsupported")
        if style.get("fit") not in {None, "cover", "contain"}:
            raise ValueError(f"{prefix}.style.fit is unsupported")
        if element_type == "qr_code":
            foreground = style.get("color", "#000000")
            qr_background = style.get("background_color", "#FFFFFF")
            for key, color in (
                ("color", foreground),
                ("background_color", qr_background),
            ):
                if not isinstance(color, str) or not re.fullmatch(
                    r"#[0-9a-fA-F]{6}", color
                ):
                    raise ValueError(
                        f"{prefix}.style.{key} must be an opaque hex color"
                    )
            if foreground.casefold() == qr_background.casefold():
                raise ValueError(
                    f"{prefix} QR foreground and background colors must differ"
                )
            quiet_zone = _optional_finite_number(
                style, "quiet_zone", f"{prefix}.style.quiet_zone", minimum=0
            )
            if quiet_zone is not None and quiet_zone > 5:
                raise ValueError(f"{prefix}.style.quiet_zone must be at most 5")
            if style.get("error_correction") not in (
                None,
                "low",
                "medium",
                "quartile",
                "high",
            ):
                raise ValueError(
                    f"{prefix}.style.error_correction is unsupported"
                )
        for key, value in data.items():
            if isinstance(value, str) and len(value) > 2000:
                raise ValueError(f"{prefix}.data.{key} is too long")
        for key in _TEXT_DATA_KEYS & data.keys():
            if not isinstance(data[key], str):
                raise ValueError(f"{prefix}.data.{key} must be a string")
        if element_type == "text" and "text" not in data:
            raise ValueError(f"{prefix}.data.text is required for text")
        if (
            element_type == "bound_text"
            and (
                not isinstance(data.get("field"), str)
                or data["field"] not in SUPPORTED_BINDING_FIELDS
            )
        ):
            raise ValueError(f"{prefix} has an unknown student field binding")
        if element_type == "custom_field_text":
            field_uuid = data.get("field_uuid")
            try:
                parsed_uuid = UUID(field_uuid) if isinstance(field_uuid, str) else None
            except (TypeError, ValueError):
                raise ValueError(f"{prefix}.data.field_uuid must be a UUID") from None
            if parsed_uuid is None or str(parsed_uuid) != field_uuid:
                raise ValueError(
                    f"{prefix}.data.field_uuid must be a canonical UUID"
                )
        if element_type == "qr_code":
            selectors = [
                key
                for key in ("text", "field", "field_uuid", "fields")
                if key in data
            ]
            if len(selectors) != 1:
                raise ValueError(
                    f"{prefix} QR data must define exactly one of text, field, "
                    "field_uuid, or fields"
                )
            selector = selectors[0]
            if selector == "text" and not data["text"].strip():
                raise ValueError(f"{prefix}.data.text cannot be blank")
            if selector == "field" and (
                not isinstance(data["field"], str)
                or data["field"] not in SUPPORTED_QR_BINDING_FIELDS
            ):
                raise ValueError(f"{prefix} has an unknown QR field binding")
            if selector == "field_uuid":
                field_uuid = data["field_uuid"]
                try:
                    parsed_uuid = UUID(field_uuid) if isinstance(field_uuid, str) else None
                except (TypeError, ValueError):
                    raise ValueError(
                        f"{prefix}.data.field_uuid must be a UUID"
                    ) from None
                if parsed_uuid is None or str(parsed_uuid) != field_uuid:
                    raise ValueError(
                        f"{prefix}.data.field_uuid must be a canonical UUID"
                    )
            if selector == "fields":
                fields = data["fields"]
                if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
                    raise ValueError(
                        f"{prefix}.data.fields must contain 1 through 20 bindings"
                    )
                identities: set[tuple[str, str]] = set()
                for field_index, binding in enumerate(fields):
                    binding_prefix = f"{prefix}.data.fields[{field_index}]"
                    if not isinstance(binding, dict):
                        raise ValueError(f"{binding_prefix} must be an object")
                    unsupported = set(binding) - {
                        "field",
                        "field_uuid",
                        "label",
                        "fallback",
                    }
                    if unsupported:
                        key = sorted(unsupported)[0]
                        raise ValueError(f"{binding_prefix}.{key} is unsupported")
                    for key in ("label", "fallback"):
                        value = binding.get(key)
                        if value is not None and not isinstance(value, str):
                            raise ValueError(f"{binding_prefix}.{key} must be a string")
                    binding_selectors = [
                        key for key in ("field", "field_uuid") if key in binding
                    ]
                    if len(binding_selectors) != 1:
                        raise ValueError(
                            f"{binding_prefix} must define exactly one of field or field_uuid"
                        )
                    binding_selector = binding_selectors[0]
                    binding_value = binding[binding_selector]
                    if binding_selector == "field":
                        if (
                            not isinstance(binding_value, str)
                            or binding_value not in SUPPORTED_BINDING_FIELDS
                        ):
                            raise ValueError(
                                f"{binding_prefix} has an unknown QR field binding"
                            )
                    else:
                        try:
                            parsed_uuid = (
                                UUID(binding_value)
                                if isinstance(binding_value, str)
                                else None
                            )
                        except (TypeError, ValueError):
                            raise ValueError(
                                f"{binding_prefix}.field_uuid must be a UUID"
                            ) from None
                        if parsed_uuid is None or str(parsed_uuid) != binding_value:
                            raise ValueError(
                                f"{binding_prefix}.field_uuid must be a canonical UUID"
                            )
                    identity = (binding_selector, binding_value)
                    if identity in identities:
                        raise ValueError(f"{prefix}.data.fields must be unique")
                    identities.add(identity)
                qr_format = data.get("format", "json")
                if not isinstance(qr_format, str) or qr_format not in {
                    "json",
                    "labeled_text",
                }:
                    raise ValueError(f"{prefix}.data.format is unsupported")
                if qr_format == "json" and any(
                    data.get(key, "") for key in ("prefix", "suffix")
                ):
                    raise ValueError(
                        f"{prefix} JSON QR data cannot use prefix or suffix"
                    )
            elif "format" in data:
                raise ValueError(f"{prefix}.data.format requires fields")
            fixed_bytes = sum(
                len(data.get(key, "").encode("utf-8"))
                for key in ("text", "prefix", "suffix", "fallback")
            )
            if selector == "fields":
                fixed_bytes += sum(
                    len(binding.get(key, "").encode("utf-8"))
                    for binding in data["fields"]
                    for key in ("label", "fallback")
                )
            if fixed_bytes > 1000:
                raise ValueError(
                    f"{prefix} QR fixed content must be at most 1000 UTF-8 bytes"
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
    expected_updated_at: datetime | None = None

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

    @field_validator("expected_updated_at")
    @classmethod
    def validate_expected_updated_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("expected_updated_at must include a timezone")
        return value


class CardTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: UUID
    name: str
    design: dict[str, Any]
    updated_at: datetime


class PublicDesignShareUpdate(BaseModel):
    enabled: bool


class PublicDesignShareResponse(BaseModel):
    enabled: bool
    public_token: str | None


class PublicDesignSchool(BaseModel):
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
    principal_name: str | None
    logo_url: str | None


class PublicDesignView(BaseModel):
    name: str
    design: dict[str, Any]
    school: PublicDesignSchool
