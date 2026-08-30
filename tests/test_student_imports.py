import io
import zipfile
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from app.api.student_imports import (
    _ValidatedImportRow,
    _suggest_mappings,
    _target_fields,
    _validate_import,
    commit_student_import,
)
from app.core.student_imports import parse_student_upload
from app.models.academic_session import AcademicSession
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.schemas.student_import import StudentImportMapping, StudentImportMappingItem
from app.schemas.student_import import (
    StudentImportCommitRequest,
    StudentImportPreviewResponse,
    StudentImportRowPreview,
)
from app.schemas.student import StudentCreate
from app.main import app


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)

    def all(self):
        return self.values


class _Database:
    def __init__(self, *results):
        self.results = iter(results)
        self.added = []

    def execute(self, _statement):
        return _Result(next(self.results))

    def add(self, value):
        self.added.append(value)


def _xlsx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Students" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/styles.xml",
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Full Name</t></is></c><c r="B1" t="inlineStr"><is><t>Admission No</t></is></c><c r="C1" t="inlineStr"><is><t>DOB</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>Asha</t></is></c><c r="B2"><v>101</v></c><c r="C2" s="1"><v>1</v></c></row></sheetData></worksheet>',
        )
    return output.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("students.csv", b"Full Name,Admission No,DOB\nAsha,101,1899-12-31\n"),
        ("students.xlsx", _xlsx_bytes()),
    ],
)
async def test_csv_and_xlsx_uploads_return_headers_and_rows(filename, content):
    headers, rows = await parse_student_upload(
        UploadFile(filename=filename, file=io.BytesIO(content))
    )
    assert headers == ["Full Name", "Admission No", "DOB"]
    assert rows == [
        {"Full Name": "Asha", "Admission No": "101", "DOB": "1899-12-31"}
    ]


def test_mapping_rejects_duplicate_sources_and_targets():
    with pytest.raises(ValueError, match="column can only be mapped once"):
        StudentImportMapping(
            mappings=[
                StudentImportMappingItem(source_column="Name", target_field="full_name"),
                StudentImportMappingItem(source_column="Name", target_field="admission_no"),
            ]
        )
    with pytest.raises(ValueError, match="target field can only be mapped once"):
        StudentImportMapping(
            mappings=[
                StudentImportMappingItem(source_column="Name", target_field="full_name"),
                StudentImportMappingItem(source_column="Student", target_field="full_name"),
            ]
        )


def test_header_suggestions_are_deterministic_and_one_to_one():
    fields = _target_fields([])
    suggestions = _suggest_mappings(
        ["Academic Session", "Class", "Section", "Admission No", "Full Name"],
        fields,
    )
    assert {item.target_field for item in suggestions} == {
        "academic_session",
        "class",
        "section",
        "admission_no",
        "full_name",
    }


def test_import_routes_expose_upload_preview_and_commit():
    paths = app.openapi()["paths"]
    assert set(paths["/schools/{school_uuid}/students/imports/upload"]) == {"post"}
    assert set(
        paths["/schools/{school_uuid}/students/imports/{upload_id}/preview"]
    ) == {"post"}
    assert set(
        paths["/schools/{school_uuid}/students/imports/{upload_id}/commit"]
    ) == {"post"}


def test_preview_detects_duplicate_upload_rows_without_student_writes():
    session = AcademicSession(id=1, uuid=uuid4(), school_id=10, name="2026-27")
    school_class = SchoolClass(id=2, uuid=uuid4(), school_id=10, name="Grade 1")
    section = Section(id=3, uuid=uuid4(), class_id=2, name="A")
    db = _Database(
        [],
        [session],
        [school_class],
        [section],
        [],
        [],
        [],
    )
    headers = ["Session", "Class", "Section", "Admission", "Name"]
    row = {
        "Session": "2026-27",
        "Class": "Grade 1",
        "Section": "A",
        "Admission": "A-1",
        "Name": "Asha",
    }
    manifest = {"headers": headers, "rows": [row, row.copy()]}
    payload = StudentImportMapping(
        mappings=[
            StudentImportMappingItem(source_column="Session", target_field="academic_session"),
            StudentImportMappingItem(source_column="Class", target_field="class"),
            StudentImportMappingItem(source_column="Section", target_field="section"),
            StudentImportMappingItem(source_column="Admission", target_field="admission_no"),
            StudentImportMappingItem(source_column="Name", target_field="full_name"),
        ]
    )

    preview, _ = _validate_import(db, 10, uuid4(), manifest, payload)

    assert preview.valid_rows == 1
    assert preview.invalid_rows == 1
    assert preview.duplicate_rows == 1
    assert preview.can_import is False
    assert db.added == []


def test_missing_required_mapping_is_rejected_before_row_processing():
    db = _Database([])
    with pytest.raises(HTTPException, match="Required target is not mapped"):
        _validate_import(
            db,
            10,
            uuid4(),
            {"headers": ["Name"], "rows": [{"Name": "Asha"}]},
            StudentImportMapping(
                mappings=[StudentImportMappingItem(source_column="Name", target_field="full_name")]
            ),
        )


def test_commit_requires_explicit_confirmation_before_any_database_work():
    db = _Database()
    with pytest.raises(HTTPException, match="Explicit confirmation"):
        commit_student_import(
            uuid4(),
            uuid4(),
            StudentImportCommitRequest(mappings=[], confirmed=False),
            db=db,
            current_user=object(),
        )
    assert db.added == []


def test_commit_adds_all_rows_then_flushes_and_commits_once(monkeypatch):
    class _CommitDatabase:
        def __init__(self):
            self.added = []
            self.flush_count = 0
            self.commit_count = 0
            self.rollback_count = 0

        def add(self, value):
            self.added.append(value)

        def flush(self):
            self.flush_count += 1

        def commit(self):
            self.commit_count += 1

        def rollback(self):
            self.rollback_count += 1

    upload_id = uuid4()
    school_uuid = uuid4()
    session_uuid = uuid4()
    class_uuid = uuid4()
    section_uuid = uuid4()
    rows = []
    for index in range(2):
        data = StudentCreate(
            session_uuid=session_uuid,
            class_uuid=class_uuid,
            section_uuid=section_uuid,
            admission_no=f"A-{index}",
            full_name=f"Student {index}",
        )
        rows.append(
            _ValidatedImportRow(
                preview=StudentImportRowPreview(row_number=index + 2, values={}),
                student_data=data,
                session_id=1,
                class_id=2,
                section_id=3,
                custom_fields=[],
            )
        )
    preview = StudentImportPreviewResponse(
        upload_id=upload_id,
        total_rows=2,
        valid_rows=2,
        invalid_rows=0,
        duplicate_rows=0,
        can_import=True,
        rows=[row.preview for row in rows],
    )
    monkeypatch.setattr(
        "app.api.student_imports._authorize",
        lambda db, current_user, requested_school_uuid: type("School", (), {"id": 10})(),
    )
    monkeypatch.setattr("app.api.student_imports.load_import_manifest", lambda **kwargs: {})
    monkeypatch.setattr(
        "app.api.student_imports._validate_import",
        lambda db, school_id, requested_upload_id, manifest, payload: (preview, rows),
    )
    deleted = []
    monkeypatch.setattr("app.api.student_imports.delete_import_manifest", deleted.append)
    db = _CommitDatabase()

    summary = commit_student_import(
        school_uuid,
        upload_id,
        StudentImportCommitRequest(mappings=[], confirmed=True),
        db=db,
        current_user=type("User", (), {"id": 5})(),
    )

    assert len(db.added) == 2
    assert db.flush_count == 1
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert deleted == [upload_id]
    assert summary.imported_count == 2
