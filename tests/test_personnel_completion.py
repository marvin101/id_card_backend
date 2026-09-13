import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from app.api import personnel_grid, personnel_imports
from app.api.bulk_personnel_photos import _resolved_entries
from app.core.bulk_student_photos import inspect_zip
from app.core.student_imports import load_import_manifest, save_import_manifest
from app.main import app
from app.models.personnel import Personnel
from app.schemas.personnel import PersonnelType
from app.schemas.personnel_grid import PersonnelGridPatchRequest, PersonnelGridRowPatch
from app.schemas.personnel_import import PersonnelImportMapping, PersonnelImportMappingItem


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, values=None, scalar=None):
        self.values = values or []
        self.scalar = scalar

    def scalars(self):
        return _Scalars(self.values)

    def scalar_one(self):
        return self.scalar

    def all(self):
        return self.values


class _Db:
    def __init__(self, *results):
        self.results = iter(results)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, _statement):
        return next(self.results)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _personnel(*, employee_no="EMP-1", personnel_type="teacher"):
    return Personnel(
        id=41,
        uuid=uuid4(),
        school_id=10,
        personnel_type=personnel_type,
        employee_no=employee_no,
        full_name="Asha Singh",
        designation="Teacher",
        department="Science",
        is_active=True,
        updated_at=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
        custom_field_values=[],
    )


def test_personnel_completion_routes_are_registered():
    paths = app.openapi()["paths"]
    prefix = "/schools/{school_uuid}"
    assert set(paths[f"{prefix}/personnel/imports/upload"]) == {"post"}
    assert set(paths[f"{prefix}/personnel/imports/template"]) == {"get"}
    assert set(paths[f"{prefix}/personnel/imports/{{upload_id}}/preview"]) == {"post"}
    assert set(paths[f"{prefix}/personnel/imports/{{upload_id}}/commit"]) == {"post"}
    assert set(paths[f"{prefix}/personnel/grid"]) == {"get", "patch"}
    assert set(paths[f"{prefix}/personnel-photos/bulk/upload"]) == {"post"}


def test_personnel_import_fields_exclude_student_only_fields():
    fields = personnel_imports._target_fields([])
    keys = {field.key for field in fields}
    assert {"employee_no", "full_name", "designation", "department"}.issubset(keys)
    assert not {"admission_no", "roll_no", "class", "section"}.intersection(keys)


def test_personnel_import_mapping_is_one_to_one():
    with pytest.raises(ValueError, match="column can only be mapped once"):
        PersonnelImportMapping(
            mappings=[
                PersonnelImportMappingItem(source_column="Name", target_field="full_name"),
                PersonnelImportMappingItem(source_column="Name", target_field="employee_no"),
            ]
        )


def test_personnel_preview_detects_duplicate_employee_number_without_writes():
    db = _Db(_Result([]), _Result([]), _Result([]), _Result([]))
    manifest = {
        "headers": ["Employee", "Name"],
        "rows": [
            {"Employee": "EMP-7", "Name": "Asha"},
            {"Employee": "EMP-7", "Name": "Mira"},
        ],
    }
    mapping = PersonnelImportMapping(
        mappings=[
            PersonnelImportMappingItem(source_column="Employee", target_field="employee_no"),
            PersonnelImportMappingItem(source_column="Name", target_field="full_name"),
        ]
    )
    preview, _ = personnel_imports._validate_import(
        db, 10, uuid4(), PersonnelType.TEACHER, manifest, mapping
    )
    assert preview.valid_rows == 1
    assert preview.invalid_rows == 1
    assert preview.duplicate_rows == 1
    assert preview.can_import is False
    assert db.added == []


def test_import_manifest_purpose_prevents_student_personnel_replay(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.student_imports.IMPORT_DIR", tmp_path)
    school_uuid = uuid4()
    upload_id = save_import_manifest(
        school_uuid=school_uuid,
        user_id=7,
        filename="teachers.csv",
        headers=["Employee Number"],
        rows=[{"Employee Number": "EMP-1"}],
        purpose="personnel:teacher",
    )
    assert load_import_manifest(
        upload_id=upload_id,
        school_uuid=school_uuid,
        user_id=7,
        purpose="personnel:teacher",
    )["purpose"] == "personnel:teacher"
    with pytest.raises(HTTPException) as raised:
        load_import_manifest(
            upload_id=upload_id,
            school_uuid=school_uuid,
            user_id=7,
            purpose="student",
        )
    assert raised.value.status_code == 404


def test_personnel_grid_valid_update_audits_only_actual_change(monkeypatch):
    record = _personnel()
    school = SimpleNamespace(id=10, uuid=uuid4())
    monkeypatch.setattr(personnel_grid, "get_active_school", lambda *_: school)
    monkeypatch.setattr(personnel_grid, "require_identity_data_access", lambda *_args, **_kwargs: None)
    db = _Db(
        _Result([record]),
        _Result([]),
        _Result([]),
        _Result([record]),
        _Result([record]),
    )
    response = personnel_grid.patch_personnel_grid(
        school.uuid,
        PersonnelGridPatchRequest(
            rows=[
                PersonnelGridRowPatch(
                    personnel_uuid=record.uuid,
                    expected_updated_at=record.updated_at,
                    system_fields={"department": "Mathematics", "full_name": "Asha Singh"},
                )
            ]
        ),
        db=db,
        current_user=SimpleNamespace(id=9),
    )
    assert response.updated_count == 1
    assert record.department == "Mathematics"
    assert db.commits == 1
    assert [(event.field_name, event.old_value, event.new_value) for event in db.added] == [
        ("department", "Science", "Mathematics")
    ]


def test_personnel_grid_get_is_bounded_type_scoped_and_returns_filter_metadata(monkeypatch):
    record = _personnel()
    school = SimpleNamespace(id=10, uuid=uuid4())
    monkeypatch.setattr(personnel_grid, "get_active_school", lambda *_: school)
    monkeypatch.setattr(personnel_grid, "require_identity_data_access", lambda *_args, **_kwargs: None)
    db = _Db(
        _Result([]),
        _Result([("Science", "Teacher")]),
        _Result(scalar=1),
        _Result([record]),
    )
    response = personnel_grid.get_personnel_grid(
        school.uuid,
        personnel_type=PersonnelType.TEACHER,
        limit=50,
        offset=0,
        search="Asha",
        active=True,
        department="Science",
        designation="Teacher",
        db=db,
        current_user=SimpleNamespace(role="card_operator"),
    )
    assert response.total == 1
    assert response.limit == 50
    assert response.personnel_type == PersonnelType.TEACHER
    assert response.rows[0].employee_no == "EMP-1"
    assert response.departments == ["Science"]
    assert response.designations == ["Teacher"]


def test_personnel_grid_conflict_is_structured_and_atomic(monkeypatch):
    record = _personnel()
    school = SimpleNamespace(id=10, uuid=uuid4())
    monkeypatch.setattr(personnel_grid, "get_active_school", lambda *_: school)
    monkeypatch.setattr(personnel_grid, "require_identity_data_access", lambda *_args, **_kwargs: None)
    db = _Db(_Result([record]), _Result([]))
    response = personnel_grid.patch_personnel_grid(
        school.uuid,
        PersonnelGridPatchRequest(
            rows=[
                PersonnelGridRowPatch(
                    personnel_uuid=record.uuid,
                    expected_updated_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                    system_fields={"full_name": "Changed"},
                )
            ]
        ),
        db=db,
        current_user=SimpleNamespace(id=9),
    )
    assert response.status_code == 409
    assert str(record.uuid).encode() in response.body
    assert record.full_name == "Asha Singh"
    assert db.commits == 0


def test_personnel_grid_rejects_duplicate_employee_number_before_mutation(monkeypatch):
    first = _personnel(employee_no="EMP-1")
    second = _personnel(employee_no="EMP-2")
    second.id = 42
    second.uuid = uuid4()
    school = SimpleNamespace(id=10, uuid=uuid4())
    monkeypatch.setattr(personnel_grid, "get_active_school", lambda *_: school)
    monkeypatch.setattr(personnel_grid, "require_identity_data_access", lambda *_args, **_kwargs: None)
    db = _Db(
        _Result([first]),
        _Result([]),
        _Result([first, second]),
    )
    response = personnel_grid.patch_personnel_grid(
        school.uuid,
        PersonnelGridPatchRequest(
            rows=[PersonnelGridRowPatch(personnel_uuid=first.uuid, system_fields={"employee_no": "EMP-2"})]
        ),
        db=db,
        current_user=SimpleNamespace(id=9),
    )
    assert response.status_code == 422
    assert first.employee_no == "EMP-1"
    assert db.commits == 0


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def test_personnel_photo_zip_matches_employee_number_and_rejects_duplicates():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("EMP-7.png", _png())
        archive.writestr("emp-7.jpg", _png())
    rows = inspect_zip(
        output.getvalue(),
        identifier_key="employee_no",
        identifier_label="employee number",
    )
    assert rows[0]["employee_no"] == "EMP-7"
    assert rows[0]["status"] == "pending"
    assert rows[1]["status"] == "invalid"
    assert "employee number" in rows[1]["detail"]


def test_personnel_photo_matching_is_type_scoped():
    record = _personnel()
    ready = _resolved_entries(
        [{"filename": "EMP-1.png", "employee_no": "EMP-1", "status": "pending"}],
        {"emp-1": record},
    )[0]
    missing = _resolved_entries(
        [{"filename": "EMP-1.png", "employee_no": "EMP-1", "status": "pending"}],
        {},
    )[0]
    assert ready["status"] == "ready"
    assert ready["personnel_uuid"] == str(record.uuid)
    assert missing["status"] == "unmatched"
