from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.card_template import CardTemplateUpdate, validate_design_document


def _document():
    return {
        "schema_version": 2,
        "canvas": {
            "width": 85.6,
            "height": 53.98,
            "orientation": "landscape",
            "background_color": "#FFFFFF",
            "background_image": None,
        },
        "elements": [
            {
                "id": "student-name",
                "type": "bound_text",
                "x": 20.0,
                "y": 10.0,
                "width": 45.0,
                "height": 6.0,
                "rotation": 0.0,
                "z_index": 2,
                "locked": False,
                "visible": True,
                "style": {"font_size": 4.0, "font_weight": 700, "alignment": "center", "color": "#242C61"},
                "data": {"field": "full_name", "fallback": "Student name"},
            },
            {
                "id": "house",
                "type": "custom_field_text",
                "x": 20.0,
                "y": 18.0,
                "width": 30.0,
                "height": 5.0,
                "rotation": 0.0,
                "z_index": 3,
                "locked": False,
                "visible": True,
                "style": {"font_size": 3.0, "color": "#111111"},
                "data": {"field_uuid": str(uuid4()), "label": "House", "fallback": "House"},
            },
        ],
        "settings": {"grid_enabled": True, "grid_size": 2.0, "snap_enabled": True},
    }


def test_legacy_v1_template_remains_valid():
    legacy = {"version": 1, "school_title": "Example", "primary_color": "#242c61"}
    assert validate_design_document(legacy) is legacy


def test_v2_template_saves_and_round_trips():
    document = _document()
    payload = CardTemplateUpdate(name="  Production card  ", design=document)
    assert payload.name == "Production card"
    assert payload.model_dump()["design"] == document


def test_non_default_canvas_size_is_accepted():
    document = _document()
    document["canvas"].update(
        {"width": 100.0, "height": 70.0, "orientation": "landscape"}
    )
    assert CardTemplateUpdate(name="Custom", design=document).design["canvas"]["width"] == 100.0


@pytest.mark.parametrize(("width", "height"), [(0, 53.98), (-1, 53.98), (10, 53.98)])
def test_invalid_canvas_size_is_rejected(width, height):
    document = _document()
    document["canvas"].update({"width": width, "height": height})
    with pytest.raises(ValidationError, match="greater than 10"):
        CardTemplateUpdate(name="Invalid", design=document)


def test_canvas_orientation_must_match_dimensions():
    document = _document()
    document["canvas"].update(
        {"width": 53.98, "height": 85.6, "orientation": "landscape"}
    )
    with pytest.raises(ValidationError, match="must be portrait"):
        CardTemplateUpdate(name="Invalid orientation", design=document)


@pytest.mark.parametrize("version", [0, 3, "2"])
def test_invalid_schema_version_is_rejected(version):
    document = _document()
    document["schema_version"] = version
    with pytest.raises(ValidationError, match="Unsupported"):
        CardTemplateUpdate(name="Card", design=document)


def test_duplicate_element_ids_are_rejected():
    document = _document()
    duplicate = deepcopy(document["elements"][0])
    document["elements"].append(duplicate)
    with pytest.raises(ValidationError, match="unique"):
        CardTemplateUpdate(name="Card", design=document)


def test_unsupported_element_type_is_rejected():
    document = _document()
    document["elements"][0]["type"] = "script"
    with pytest.raises(ValidationError, match="unsupported"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(("key", "value"), [("x", float("nan")), ("width", 0), ("height", -1)])
def test_invalid_numeric_geometry_is_rejected(key, value):
    document = _document()
    document["elements"][0][key] = value
    with pytest.raises(ValidationError):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    ("key", "value"),
    [("x", -0.1), ("y", 2000.1), ("width", 2000.1), ("rotation", 360.1)],
)
def test_geometry_outside_flutter_supported_range_is_rejected(key, value):
    document = _document()
    document["elements"][0][key] = value
    with pytest.raises(ValidationError, match="supported range"):
        CardTemplateUpdate(name="Card", design=document)


def test_system_and_custom_bindings_are_accepted_safely():
    payload = CardTemplateUpdate(name="Card", design=_document())
    assert payload.design["elements"][0]["data"]["field"] == "full_name"


def test_custom_binding_uuid_must_use_the_canonical_wire_format():
    document = _document()
    document["elements"][1]["data"]["field_uuid"] = (
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    )
    with pytest.raises(ValidationError, match="canonical UUID"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_flutter_school_profile_bindings_are_accepted(field):
    document = _document()
    document["elements"][0]["data"]["field"] = field
    payload = CardTemplateUpdate(name="Card", design=document)
    assert payload.design["elements"][0]["data"]["field"] == field


def test_unknown_system_binding_is_rejected():
    document = _document()
    document["elements"][0]["data"]["field"] = "password_hash"
    with pytest.raises(ValidationError, match="unknown student field"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("canvas", "background_image"), 42, "background_image"),
        (("elements", 0, "z_index"), 1.5, "integer"),
        (("elements", 0, "style", "font_weight"), "700", "number"),
        (("elements", 0, "style", "max_lines"), 0, "positive integer"),
        (("elements", 0, "style", "alignment"), "justify", "unsupported"),
        (("elements", 0, "data", "fallback"), {"text": "unsafe"}, "string"),
        (("settings", "grid_enabled"), "yes", "boolean"),
        (("settings", "grid_size"), float("nan"), "finite"),
    ],
)
def test_malformed_flutter_known_values_are_rejected(path, value, message):
    document = _document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="Card", design=document)


def test_unknown_style_data_and_settings_keys_are_preserved():
    document = _document()
    document["elements"][0]["style"]["future_style"] = {"value": 1}
    document["elements"][0]["data"]["future_data"] = ["value"]
    document["settings"]["future_setting"] = "value"
    assert CardTemplateUpdate(name="Card", design=document).design == document


def test_name_is_trimmed_before_length_validation():
    payload = CardTemplateUpdate(name=f"  {'x' * 120}  ", design=_document())
    assert payload.name == "x" * 120


def test_non_string_name_is_a_validation_error():
    with pytest.raises(ValidationError):
        CardTemplateUpdate(name=42, design=_document())
