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


def _qr_element(data=None, style=None):
    return {
        "id": "student-qr",
        "type": "qr_code",
        "x": 60.0,
        "y": 30.0,
        "width": 18.0,
        "height": 18.0,
        "rotation": 0.0,
        "z_index": 4,
        "locked": False,
        "visible": True,
        "style": {
            "color": "#000000",
            "background_color": "#FFFFFF",
            "quiet_zone": 1.0,
            "error_correction": "medium",
            **(style or {}),
        },
        "data": data if data is not None else {"text": "CAMPUS-ID:123"},
    }


def _barcode_element(symbology="code128", data=None, style=None):
    return {
        "id": "student-barcode",
        "type": "barcode",
        "x": 25.0,
        "y": 30.0,
        "width": 50.0 if symbology != "data_matrix" else 18.0,
        "height": 14.0 if symbology != "data_matrix" else 18.0,
        "rotation": 0.0,
        "z_index": 4,
        "locked": False,
        "visible": True,
        "style": {
            "color": "#000000",
            "background_color": "#FFFFFF",
            "quiet_zone": 1.0,
            "show_text": symbology != "data_matrix",
            "font_size": 2.5,
            **(style or {}),
        },
        "data": {
            "symbology": symbology,
            **(data if data is not None else {"text": "CAMPUS-ID:123"}),
        },
    }


def test_legacy_v1_template_remains_valid():
    legacy = {"version": 1, "school_title": "Example", "primary_color": "#242c61"}
    assert validate_design_document(legacy) is legacy


def test_v2_template_saves_and_round_trips():
    document = _document()
    payload = CardTemplateUpdate(name="  Production card  ", design=document)
    assert payload.name == "Production card"
    assert payload.model_dump()["design"] == document


def test_optional_back_design_uses_the_same_bounded_contract():
    front = _document()
    back = _document()
    back["elements"][0]["data"]["fallback"] = "Back side"
    payload = CardTemplateUpdate(
        name="Two-sided card",
        design=front,
        back_design=back,
    )
    assert payload.back_design == back

    invalid_back = deepcopy(back)
    invalid_back["canvas"]["width"] = 0
    with pytest.raises(ValidationError, match="greater than 10"):
        CardTemplateUpdate(
            name="Invalid back",
            design=front,
            back_design=invalid_back,
        )


def test_back_design_may_be_explicitly_removed_or_omitted():
    document = _document()
    removed = CardTemplateUpdate(
        name="Front only",
        design=document,
        back_design=None,
    )
    omitted = CardTemplateUpdate(name="Legacy client", design=document)
    assert removed.back_design is None
    assert "back_design" in removed.model_fields_set
    assert "back_design" not in omitted.model_fields_set


def test_front_and_back_canvas_dimensions_must_match():
    front = _document()
    back = _document()
    back["canvas"]["height"] += 1
    with pytest.raises(ValidationError, match="canvas dimensions must match"):
        CardTemplateUpdate(name="Mismatched sides", design=front, back_design=back)


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


@pytest.mark.parametrize("version", [0, 3])
def test_invalid_schema_version_is_rejected(version):
    document = _document()
    document["schema_version"] = version
    with pytest.raises(ValidationError, match="Unsupported"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize("version", [True, False, "2", 2.0])
def test_schema_version_must_be_an_integer_not_a_coercible_value(version):
    document = _document()
    document["schema_version"] = version
    with pytest.raises(ValidationError, match="must be an integer"):
        CardTemplateUpdate(name="Card", design=document)


def test_duplicate_element_ids_are_rejected():
    document = _document()
    duplicate = deepcopy(document["elements"][0])
    document["elements"].append(duplicate)
    with pytest.raises(ValidationError, match="unique"):
        CardTemplateUpdate(name="Card", design=document)


def test_duplicate_z_indices_are_rejected_for_deterministic_layering():
    document = _document()
    document["elements"][1]["z_index"] = document["elements"][0]["z_index"]
    with pytest.raises(ValidationError, match="z_index values must be unique"):
        CardTemplateUpdate(name="Card", design=document)


def test_element_ids_cannot_have_surrounding_whitespace():
    document = _document()
    document["elements"][0]["id"] = " student-name "
    with pytest.raises(ValidationError, match="non-empty string"):
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


@pytest.mark.parametrize(
    "data",
    [
        {"text": "CAMPUS-ID:123"},
        {"field": "admission_no", "prefix": "CAMPUS-ID:"},
        {"field_uuid": str(uuid4()), "fallback": "No value"},
        {
            "fields": [
                {"field": "full_name", "label": "Full name"},
                {"field": "admission_no", "label": "Admission number"},
                {"field_uuid": str(uuid4()), "label": "House"},
            ],
            "format": "json",
        },
    ],
)
def test_qr_static_system_and_custom_payloads_round_trip(data):
    document = _document()
    document["elements"].append(_qr_element(data=data))

    payload = CardTemplateUpdate(name="QR card", design=document)

    assert payload.design["elements"][-1]["data"] == data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "exactly one"),
        ({"text": "one", "field": "admission_no"}, "exactly one"),
        ({"text": "one", "fields": [{"field": "full_name"}]}, "exactly one"),
        ({"text": "   "}, "cannot be blank"),
        ({"field": "password_hash"}, "unknown QR field"),
        ({"field": []}, "unknown QR field"),
        ({"field_uuid": "not-a-uuid"}, "must be a UUID"),
        ({"field_uuid": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}, "canonical UUID"),
    ],
)
def test_qr_payload_selector_is_strict(data, message):
    document = _document()
    document["elements"].append(_qr_element(data=data))

    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="QR card", design=document)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"width": 11.9, "height": 11.9}, "at least 12"),
        ({"width": 18, "height": 17}, "must be square"),
        ({"style": {"color": "#00000080"}}, "opaque hex color"),
        ({"style": {"background_color": "#000000"}}, "colors must differ"),
        ({"style": {"quiet_zone": 5.1}}, "at most 5"),
        ({"style": {"error_correction": "maximum"}}, "unsupported"),
        ({"style": {"error_correction": []}}, "unsupported"),
    ],
)
def test_qr_geometry_and_style_are_scannable(change, message):
    document = _document()
    element = _qr_element()
    if "style" in change:
        element["style"].update(change["style"])
    else:
        element.update(change)
    document["elements"].append(element)

    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="QR card", design=document)


def test_qr_fixed_content_has_a_utf8_byte_limit():
    document = _document()
    document["elements"].append(_qr_element(data={"text": "é" * 501}))

    with pytest.raises(ValidationError, match="1000 UTF-8 bytes"):
        CardTemplateUpdate(name="QR card", design=document)


@pytest.mark.parametrize(
    ("symbology", "data"),
    [
        ("code128", {"text": "CAMPUS-ID:123"}),
        ("code39", {"text": "CAMPUS-ID 123"}),
        ("ean13", {"text": "5901234123457"}),
        ("data_matrix", {"text": "Student: आशा"}),
        ("code128", {"field": "admission_no", "prefix": "ID:"}),
        (
            "data_matrix",
            {
                "fields": [
                    {"field": "full_name", "label": "Full name"},
                    {"field": "admission_no", "label": "Admission"},
                ],
                "format": "json",
            },
        ),
    ],
)
def test_barcode_formats_and_binding_sources_round_trip(symbology, data):
    document = _document()
    document["elements"].append(_barcode_element(symbology, data=data))

    payload = CardTemplateUpdate(name="Barcode card", design=document)

    assert payload.design["elements"][-1]["data"]["symbology"] == symbology


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"data": {"symbology": "upc", "text": "123"}}, "symbology is unsupported"),
        ({"data": {"symbology": "ean13", "text": "ABC"}}, "12 or 13 digits"),
        ({"data": {"symbology": "code39", "text": "lowercase"}}, "unsupported characters"),
        ({"data": {"symbology": "code128", "text": "é"}}, "printable ASCII"),
        ({"width": 24.9}, "at least 25 by 10"),
        (
            {"symbology": "data_matrix", "width": 18, "height": 17},
            "must be square",
        ),
        (
            {"symbology": "data_matrix", "style": {"show_text": True}},
            "unsupported for Data Matrix",
        ),
        ({"style": {"show_text": "yes"}}, "must be a boolean"),
        ({"data": {"symbology": "code128", "field": "verification_url"}}, "unknown barcode field"),
    ],
)
def test_barcode_contract_rejects_invalid_format_content_and_geometry(change, message):
    document = _document()
    symbology = change.get("symbology", "code128")
    element = _barcode_element(symbology)
    if "data" in change:
        element["data"] = change["data"]
    if "style" in change:
        element["style"].update(change["style"])
    for key in ("width", "height"):
        if key in change:
            element[key] = change[key]
    document["elements"].append(element)

    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="Barcode card", design=document)


@pytest.mark.parametrize(
    ("fields", "format", "message"),
    [
        ([], "json", "1 through 20"),
        ([{"field": "full_name"}] * 21, "json", "1 through 20"),
        (["full_name"], "json", "must be an object"),
        ([{}], "json", "exactly one"),
        ([{"field": "full_name", "field_uuid": str(uuid4())}], "json", "exactly one"),
        ([{"field": "password_hash"}], "json", "unknown QR field"),
        ([{"field_uuid": "bad"}], "json", "must be a UUID"),
        ([{"field": "full_name"}, {"field": "full_name"}], "json", "unique"),
        ([{"field": "full_name", "extra": "x"}], "json", "unsupported"),
        ([{"field": "full_name"}], "xml", "format is unsupported"),
    ],
)
def test_qr_multiple_field_contract_is_strict(fields, format, message):
    document = _document()
    document["elements"].append(
        _qr_element(data={"fields": fields, "format": format})
    )

    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="QR card", design=document)


def test_structured_json_qr_rejects_ambiguous_wrapping_text():
    document = _document()
    document["elements"].append(
        _qr_element(
            data={
                "fields": [{"field": "full_name", "label": "Full name"}],
                "format": "json",
                "prefix": "Student:",
            }
        )
    )

    with pytest.raises(ValidationError, match="cannot use prefix or suffix"):
        CardTemplateUpdate(name="QR card", design=document)


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


def test_combined_class_section_binding_is_accepted():
    document = _document()
    document["elements"][0]["data"]["field"] = "class_section"

    payload = CardTemplateUpdate(name="Card", design=document)

    assert payload.design["elements"][0]["data"]["field"] == "class_section"


def test_unknown_system_binding_is_rejected():
    document = _document()
    document["elements"][0]["data"]["field"] = "password_hash"
    with pytest.raises(ValidationError, match="unknown student field"):
        CardTemplateUpdate(name="Card", design=document)


def test_static_text_requires_text_data():
    document = _document()
    document["elements"][0]["type"] = "text"
    document["elements"][0]["data"] = {}
    with pytest.raises(ValidationError, match=r"data\.text is required"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    ("element_type", "style_key", "style_value"),
    [
        ("rectangle", "font_size", 3),
        ("bound_text", "fit", "cover"),
        ("line", "corner_radius", 1),
        ("student_photo", "fill_color", "#FFFFFF"),
    ],
)
def test_known_style_properties_are_scoped_to_supported_element_types(
    element_type, style_key, style_value
):
    document = _document()
    element = document["elements"][0]
    element["type"] = element_type
    element["style"] = {style_key: style_value}
    element["data"] = {} if element_type not in {"bound_text"} else element["data"]
    with pytest.raises(ValidationError, match=rf"style\.{style_key} is unsupported"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    ("element_type", "data"),
    [
        ("rectangle", {"text": "ignored"}),
        ("student_photo", {"field": "full_name"}),
        ("text", {"text": "Name", "field_uuid": str(uuid4())}),
    ],
)
def test_known_data_properties_are_scoped_to_supported_element_types(
    element_type, data
):
    document = _document()
    document["elements"][0]["type"] = element_type
    document["elements"][0]["style"] = {}
    document["elements"][0]["data"] = data
    with pytest.raises(ValidationError, match=r"data\..* is unsupported"):
        CardTemplateUpdate(name="Card", design=document)


@pytest.mark.parametrize(
    ("element_type", "style_key", "style_value", "message"),
    [
        ("bound_text", "font_size", 20.1, "at most 20"),
        ("rectangle", "border_width", 10.1, "at most 10"),
        ("rectangle", "corner_radius", 30.1, "at most 30"),
        ("bound_text", "max_lines", 101, "no greater than 100"),
        ("line", "border_width", 0, "positive for line"),
    ],
)
def test_style_numbers_stay_within_editor_supported_limits(
    element_type, style_key, style_value, message
):
    document = _document()
    element = document["elements"][0]
    element["type"] = element_type
    element["style"] = {style_key: style_value}
    element["data"] = (
        {"field": "full_name"} if element_type == "bound_text" else {}
    )
    with pytest.raises(ValidationError, match=message):
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

@pytest.mark.parametrize("shape", ["rectangle", "rounded", "oval"])
def test_photo_image_shape_round_trips(shape):
    document = _document()
    element = deepcopy(document["elements"][0])
    element.update(id="photo-shape", type="student_photo", z_index=4, data={}, style={"image_shape": shape})
    document["elements"].append(element)
    assert CardTemplateUpdate(name="Shape card", design=document).design["elements"][-1]["style"]["image_shape"] == shape


def test_photo_image_shape_rejects_unknown_value():
    document = _document()
    element = deepcopy(document["elements"][0])
    element.update(id="photo-shape", type="student_photo", z_index=4, data={}, style={"image_shape": "triangle"})
    document["elements"].append(element)
    with pytest.raises(ValidationError, match="image_shape"):
        CardTemplateUpdate(name="Shape card", design=document)


NEW_DESIGNER_ELEMENTS = {
    "principal_signature": (
        {"fit": "contain", "border_color": "#242C61", "border_width": 0.5,
         "corner_radius": 1.0, "image_shape": "rounded"},
        {},
    ),
    "blood_drop": (
        {"fill_color": "#C62828", "border_color": "#C62828",
         "border_width": 0.5, "corner_radius": 0.0},
        {"field": "blood_group", "fallback": "BG"},
    ),
    **{
        kind: (
            {"fill_color": "#E8EEF8", "border_color": "#242C61",
             "border_width": 0.5,
             "corner_radius": 3.0 if kind == "rounded_rectangle" else 0.0},
            {},
        )
        for kind in ("rounded_rectangle", "ellipse", "circle", "triangle")
    },
}


@pytest.mark.parametrize("element_type", NEW_DESIGNER_ELEMENTS)
def test_new_designer_elements_round_trip_on_both_sides(element_type):
    style, data = NEW_DESIGNER_ELEMENTS[element_type]
    front = _document()
    element = deepcopy(front["elements"][0])
    element.update(
        id=element_type, type=element_type, z_index=4, width=20.0,
        height=20.0 if element_type in {"circle", "blood_drop"} else 16.0,
        style=style, data=data,
    )
    front["elements"].append(element)
    back = deepcopy(front)
    payload = CardTemplateUpdate(
        name="New elements", design=front, back_design=back,
    )
    assert payload.design["elements"][-1] == element
    assert payload.back_design["elements"][-1] == element


@pytest.mark.parametrize(
    ("element_type", "style", "data", "message"),
    [
        ("principal_signature", {}, {"field": "principal_name"}, "data.field is unsupported"),
        ("principal_signature", {"fit": "stretch"}, {}, "style.fit is unsupported"),
        ("principal_signature", {"image_shape": "triangle"}, {}, "style.image_shape is unsupported"),
        ("blood_drop", {}, {}, "data.field must be blood_group"),
        ("blood_drop", {}, {"field": "full_name"}, "data.field must be blood_group"),
        ("blood_drop", {}, {"field": "blood_group", "fallback": 42}, "data.fallback must be a string"),
        ("blood_drop", {}, {"field": "blood_group", "text": "O+"}, "data.text is unsupported"),
        ("blood_drop", {"fit": "contain"}, {"field": "blood_group"}, "style.fit is unsupported"),
        ("rounded_rectangle", {"font_size": 3}, {}, "style.font_size is unsupported"),
        ("ellipse", {}, {"field": "blood_group"}, "data.field is unsupported"),
        ("circle", {"image_shape": "oval"}, {}, "style.image_shape is unsupported"),
        ("triangle", {"border_width": -1}, {}, "style.border_width must be at least"),
    ],
)
def test_new_designer_elements_reject_malformed_contract(element_type, style, data, message):
    document = _document()
    document["elements"][0].update(type=element_type, style=style, data=data)
    with pytest.raises(ValidationError, match=message):
        CardTemplateUpdate(name="Invalid", design=document)


def test_v2_canvas_accepts_background_transform_properties():
    design = _document()
    design["canvas"].update({
        "background_image": "https://example.test/background.png",
        "background_opacity": 0.45,
        "background_scale": 1.75,
        "background_offset_x": 4.5,
        "background_offset_y": -3.0,
    })
    assert validate_design_document(design) == design


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("background_opacity", -0.1),
        ("background_opacity", 1.1),
        ("background_scale", 0.9),
        ("background_scale", 5.1),
        ("background_offset_x", float("inf")),
        ("background_offset_y", 501),
    ],
)
def test_v2_canvas_rejects_invalid_background_transform_properties(field, value):
    design = _document()
    design["canvas"][field] = value
    with pytest.raises(ValueError, match=field):
        validate_design_document(design)
