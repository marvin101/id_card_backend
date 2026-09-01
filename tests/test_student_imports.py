import io
import zipfile
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.api.student_imports import (
    _ValidatedImportRow,
    _resolve_target_fields,
    _suggest_mappings,
    _target_fields,
    _validate_import,
    commit_student_import,
    download_student_import_template,
)
from app.core.student_import_template import XLSX_CONTENT_TYPE, build_student_import_template
from app.core.student_imports import parse_student_upload
from app.models.academic_session import AcademicSession
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
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
    assert set(paths["/schools/{school_uuid}/students/imports/template"]) == {"get"}
    assert set(
        paths["/schools/{school_uuid}/students/imports/{upload_id}/preview"]
    ) == {"post"}
    assert set(
        paths["/schools/{school_uuid}/students/imports/{upload_id}/commit"]
    ) == {"post"}


def _definition(*, label, order, active=True, required=False):
    return SimpleNamespace(
        id=order + 1,
        uuid=uuid4(),
        label=label,
        data_type="text",
        is_required=required,
        display_order=order,
        is_active=active,
    )


def _worksheet_rows(content: bytes):
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return root, root.findall(f"{namespace}sheetData/{namespace}row")


def test_template_requires_authentication():
    with TestClient(app) as client:
        response = client.get(f"/schools/{uuid4()}/students/imports/template")

    assert response.status_code == 401


def test_template_uses_same_school_access_enforcement_as_import(monkeypatch):
    school = SimpleNamespace(id=10, school_name="Campus School")
    monkeypatch.setattr("app.api.student_imports.get_active_school", lambda *args: school)
    monkeypatch.setattr(
        "app.api.student_imports.require_card_data_access",
        lambda *args: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Only permitted users can import students")
        ),
    )

    with pytest.raises(HTTPException) as error:
        download_student_import_template(uuid4(), db=object(), current_user=object())

    assert error.value.status_code == 403


def test_school_template_contains_only_active_ordered_fields_and_no_data_rows():
    later = _definition(label="Bus Route", order=8)
    disabled = _definition(label="House", order=2, active=False)
    earlier = _definition(label="Emergency Contact", order=1, required=True)
    # The database query is authoritative for filtering and ordering; emulate its result.
    db = _Database([earlier, later])

    fields = _resolve_target_fields(db, 10)
    content = build_student_import_template(fields)
    root, rows = _worksheet_rows(content)
    xml = ElementTree.tostring(root, encoding="unicode")

    assert [field.label for field in fields[-2:]] == ["Emergency Contact", "Bus Route"]
    assert "Emergency Contact" in xml
    assert "Bus Route" in xml
    assert disabled.label not in xml
    assert len(rows) == 1
    assert rows[0].attrib["r"] == "1"
    assert 'ySplit="1"' in xml


def test_template_headers_auto_map_to_authoritative_target_field_set():
    definition = _definition(label="Transport Stop", order=0)
    fields = _target_fields([definition])
    suggestions = _suggest_mappings([field.label for field in fields], fields)

    assert [item.target_field for item in suggestions] == [field.key for field in fields]


def test_template_response_has_xlsx_headers_and_school_filename(monkeypatch):
    school = SimpleNamespace(id=10, school_name="Campus International School")
    fields = _target_fields([])
    monkeypatch.setattr("app.api.student_imports._authorize", lambda *args: school)
    monkeypatch.setattr("app.api.student_imports._resolve_target_fields", lambda *args: fields)

    response = download_student_import_template(uuid4(), db=object(), current_user=object())

    assert response.media_type == XLSX_CONTENT_TYPE
    assert response.headers["content-disposition"] == (
        'attachment; filename="student_import_template_campus_international_school.xlsx"'
    )
    assert response.body.startswith(b"PK")


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
            next_id = 1
            for value in self.added:
                if isinstance(value, Student) and value.id is None:
                    value.id = next_id
                    next_id += 1

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

    assert len(db.added) == 4
    assert [event.event_type for event in db.added[2:]] == [
        "student_created",
        "student_created",
    ]
    assert db.flush_count == 1
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert deleted == [upload_id]
    assert summary.imported_count == 2
