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


def test_system_and_custom_bindings_are_accepted_safely():
    payload = CardTemplateUpdate(name="Card", design=_document())
    assert payload.design["elements"][0]["data"]["field"] == "full_name"


def test_unknown_system_binding_is_rejected():
    document = _document()
    document["elements"][0]["data"]["field"] = "password_hash"
    with pytest.raises(ValidationError, match="unknown student field"):
        CardTemplateUpdate(name="Card", design=document)
