# CampusID Designer v2 document

Designer v2 uses physical millimetres as its canonical coordinate system. The
default landscape CR80 canvas is `85.60 × 53.98`; Flutter converts millimetres
to logical pixels only while displaying the canvas, and the PDF service uses
the same values with `PdfPageFormat.mm`. Zoom never changes saved geometry.

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
`rectangle`, and `line`. System text uses `data.field`; custom text uses the
stable `data.field_uuid` rather than a mutable label.

The API continues to return stored v1 documents unchanged. Flutter recognizes
the absence of `schema_version: 2`, converts known v1 settings into a
deterministic in-memory v2 layout, and retains the old settings under named
compatibility keys. The converted document is persisted only when the user
saves it. Unknown versions and malformed v2 geometry are rejected with HTTP
422 before storage.
