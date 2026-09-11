# CampusID Designer v2 document

Designer v2 uses physical millimetres as its canonical coordinate system. The
default landscape CR80 canvas is `85.60 × 53.98`; Flutter converts millimetres
to logical pixels only while displaying the canvas, and the PDF service uses
the same values with `PdfPageFormat.mm`. Zoom never changes saved geometry.

A template stores this document as its required `design` (front side) and may
store a second document as `back_design`. Front and back are edited
independently but must use matching canvas width and height for duplex
alignment. Omitting `back_design` from an update preserves an existing back;
an explicit `null` removes it.

```json
{
  "schema_version": 2,
  "canvas": {
    "width": 85.6,
    "height": 53.98,
    "orientation": "landscape",
    "background_color": "#FFFFFF",
    "background_image": null
  },
  "elements": [],
  "settings": {
    "grid_enabled": true,
    "grid_size": 2.0,
    "snap_enabled": true
  }
}
```

Every element has `id`, `type`, `x`, `y`, `width`, `height`, `rotation`,
`z_index`, `locked`, `visible`, `style`, and `data`. Supported types are
`text`, `bound_text`, `custom_field_text`, `student_photo`, `school_logo`,
`rectangle`, `line`, `qr_code`, and `barcode`. System bindings use `data.field`; custom
bindings use the stable `data.field_uuid` rather than a mutable label.

Element IDs cannot contain surrounding whitespace and must be unique. Layering
is explicit: every `z_index` must be an integer from `-10000` through `10000`
and must be unique within the document. Known properties are scoped by type:

| Element type | Required data | Supported known style properties |
| --- | --- | --- |
| `text` | `text` | `color`, `font_size`, `font_weight`, `max_lines`, `alignment` |
| `bound_text` | supported `field` | `color`, `font_size`, `font_weight`, `max_lines`, `alignment` |
| `custom_field_text` | canonical `field_uuid` | `color`, `font_size`, `font_weight`, `max_lines`, `alignment` |
| `student_photo`, `school_logo` | none | `fit`, `border_color`, `border_width`, `corner_radius` |
| `rectangle` | none | `fill_color`, `border_color`, `border_width`, `corner_radius` |
| `line` | none | `color`, `border_width` |
| `qr_code` | exactly one of `text`, supported `field`, canonical `field_uuid`, or `fields` | `color`, `background_color`, `quiet_zone`, `error_correction` |
| `barcode` | `symbology` plus exactly one of `text`, supported `field`, canonical `field_uuid`, or `fields` | `color`, `background_color`, `quiet_zone`, `show_text`, `font_size` |

QR elements are square and at least 12 mm on each side. `prefix`, `suffix`,
`fallback`, and `label` are optional data properties. Foreground and background
must be distinct opaque colours, `quiet_zone` is 0–5 mm, and
`error_correction` is `low`, `medium`, `quartile`, or `high`. Fixed QR content
is limited to 1000 UTF-8 bytes; Flutter applies the same limit to resolved
per-student content before bulk PDF generation.

`fields` contains 1–20 unique system/custom bindings and keeps custom fields
scoped by UUID. Multi-field payloads default to compact `json`, whose stable
keys are system field names and `custom:<field_uuid>` for custom values. The
alternative `labeled_text` format emits one `Label: value` line per selected
field. JSON payloads cannot use a prefix or suffix, preserving valid structured
data when the code is scanned.

Barcode `symbology` is `code128`, `code39`, `ean13`, or `data_matrix`. Code 128,
Code 39, and EAN-13 elements are at least 25 × 10 mm; Data Matrix is square and
at least 12 mm per side. The same single/custom/multi-field binding contract is
used as QR, except `verification_url` is deliberately unavailable. Static and
resolved content is checked against format-specific character and byte limits;
EAN-13 additionally requires 12 or 13 digits. One-dimensional formats may show
a human-readable value, while Data Matrix does not. Flutter repeats resolved
content validation before bulk PDF generation.

`verification_url` is a special single-field QR binding. It resolves to the
student's signed, expiring public capability URL and is deliberately rejected for
`bound_text` and multi-field QR payloads. This keeps the opaque link inside the
QR code and prevents accidental visible or mixed PII payloads. New QR elements
created by Flutter use this recommended source by default; existing static,
single-field, custom-field, and multi-field QR documents remain compatible.

The linked public page remains unavailable until a school administrator enables
public verification and selects the permitted disclosure fields. Disabling the
school or student setting revokes access, while regenerating a student's token
invalidates the prior link and requires cards containing it to be reprinted.
The signed credential is purpose- and audience-bound, contains no disclosed
student profile values, and is checked against the current token identifier and
credential version before any school-approved fields are returned. Legacy opaque
links remain compatible during rollout but are subject to the migrated expiry.

The editor-supported bounds are `font_size <= 20`, `border_width <= 10`,
`corner_radius <= 30`, and `max_lines <= 100`; an explicitly configured line
width must be positive. Recognized data and style properties on incompatible
element types are rejected because the renderers would ignore them. Unknown
extension properties remain preserved for forward compatibility.

The API continues to return stored v1 documents unchanged. Flutter recognizes
the absence of `schema_version: 2`, converts known v1 settings into a
deterministic in-memory v2 layout, and retains the old settings under named
compatibility keys. The converted document is persisted only when the user
saves it. Unknown versions and malformed v2 geometry are rejected with HTTP
422 before storage.
