from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException

from app.core.auto_admission import (
    DEFAULT_STREAM_OPTIONS,
    format_admission_no,
    resolve_stream_code,
    apply_auto_admission_to_existing_students,
)


def test_resolve_stream_code():
    assert resolve_stream_code("Science") == "SCI"
    assert resolve_stream_code("science") == "SCI"
    assert resolve_stream_code("Arts") == "ARTS"
    assert resolve_stream_code("Commerce") == "COM"
    assert resolve_stream_code("com") == "COM"
    assert resolve_stream_code("SCI") == "SCI"
    assert resolve_stream_code("") is None
    assert resolve_stream_code(None) is None

    # Custom options
    custom = [
        {"name": "Vocational", "code": "VOC"},
        {"name": "Humanities", "code": "HUM"},
    ]
    assert resolve_stream_code("Vocational", custom) == "VOC"
    assert resolve_stream_code("voc", custom) == "VOC"
    assert resolve_stream_code("Humanities", custom) == "HUM"
    # Fallback to standard
    assert resolve_stream_code("Science", custom) == "SCI"


def test_format_admission_no():
    assert format_admission_no("Science", "31") == "SCI/31"
    assert format_admission_no("Arts", "10") == "ARTS/10"
    assert format_admission_no("Commerce", "5") == "COM/5"
    assert format_admission_no("Science", "  42  ") == "SCI/42"
    assert format_admission_no("", "31") is None
    assert format_admission_no("Science", "") is None
    assert format_admission_no(None, "31") is None


def test_apply_auto_admission_success():
    db = MagicMock()
    school = SimpleNamespace(id=1, stream_options=DEFAULT_STREAM_OPTIONS)

    s1 = SimpleNamespace(
        id=101, school_id=1, full_name="Alice", stream="Science", roll_no="31", admission_no="OLD-1"
    )
    s2 = SimpleNamespace(
        id=102, school_id=1, full_name="Bob", stream="Arts", roll_no="12", admission_no="OLD-2"
    )
    s3 = SimpleNamespace(
        id=103, school_id=1, full_name="Charlie", stream=None, roll_no="5", admission_no="OLD-3"
    )
    s4 = SimpleNamespace(
        id=104, school_id=1, full_name="Daisy", stream="Commerce", roll_no="7", admission_no="COM/7"
    )

    db.execute.return_value.scalars.return_value.all.return_value = [s1, s2, s3, s4]

    updated = apply_auto_admission_to_existing_students(db, school)

    assert updated == 2
    assert s1.admission_no == "SCI/31"
    assert s2.admission_no == "ARTS/12"
    assert s3.admission_no == "OLD-3"  # unchanged because stream is None
    assert s4.admission_no == "COM/7"  # already had automated format


def test_apply_auto_admission_detects_duplicates():
    db = MagicMock()
    school = SimpleNamespace(id=1, stream_options=DEFAULT_STREAM_OPTIONS)

    s1 = SimpleNamespace(
        id=101, school_id=1, full_name="Alice", stream="Science", roll_no="31", admission_no="OLD-1"
    )
    s2 = SimpleNamespace(
        id=102, school_id=1, full_name="Bob", stream="Science", roll_no="31", admission_no="OLD-2"
    )

    db.execute.return_value.scalars.return_value.all.return_value = [s1, s2]

    with pytest.raises(HTTPException) as exc_info:
        apply_auto_admission_to_existing_students(db, school)

    assert exc_info.value.status_code == 409
    assert "duplicate admission numbers" in exc_info.value.detail.lower()
    assert "SCI/31" in exc_info.value.detail
