import asyncio
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.api import bulk_student_photos as bulk_api
from app.core import bulk_student_photos as bulk_core
from app.core.file_storage import StorageError


def _png_bytes(color=(20, 80, 140)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 3), color).save(output, format="PNG")
    return output.getvalue()


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in entries.items():
            archive.writestr(filename, content)
    return output.getvalue()


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        return SimpleNamespace(all=lambda: values)


class _UploadSession:
    def __init__(self, *, fail_commit=False):
        self.fail_commit = fail_commit
        self.added = []
        self.rollbacks = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _value):
        return None


class _ManifestSession:
    def __init__(self, manifest):
        self.manifest = manifest
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self.manifest)

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _student(*, photo_path=None):
    return SimpleNamespace(
        id=10,
        uuid=uuid4(),
        school_id=20,
        admission_no="A-1",
        full_name="Asha Singh",
        photo_path=photo_path,
        is_active=True,
    )


def _upload_archive(monkeypatch, session, entries):
    school_uuid = uuid4()
    school = SimpleNamespace(id=20, uuid=school_uuid)
    student = _student()
    monkeypatch.setattr(bulk_api, "_authorize", lambda *_args: school)
    monkeypatch.setattr(
        bulk_api,
        "cleanup_expired_bulk_photo_imports",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        bulk_api,
        "_student_lookup",
        lambda *_args: {"a-1": student},
    )
    archive = UploadFile(
        filename="photos.zip",
        file=io.BytesIO(_zip_bytes(entries)),
        headers={"content-type": "application/zip"},
    )
    response = asyncio.run(
        bulk_api.upload_bulk_student_photos(
            school_uuid=school_uuid,
            archive=archive,
            db=session,
            current_user=SimpleNamespace(id=1),
        )
    )
    return response, school, student


def _manifest(school_uuid, student, *, expires_at=None):
    upload_uuid = uuid4()
    temp_path = (
        f"schools/{school_uuid}/bulk-photo-imports/"
        f"{upload_uuid}/{uuid4().hex}.png"
    )
    return SimpleNamespace(
        uuid=upload_uuid,
        school_id=20,
        user_id=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
        status="previewed",
        total_files=1,
        manifest=[
            {
                "item_uuid": str(uuid4()),
                "filename": "A-1.png",
                "admission_no": "A-1",
                "match_key": "a-1",
                "extension": ".png",
                "content_type": "image/png",
                "file_size": len(_png_bytes()),
                "temp_storage_path": temp_path,
                "student_uuid": str(student.uuid),
                "student_name": student.full_name,
                "status": "ready",
                "replacement": bool(student.photo_path),
                "has_existing_photo": bool(student.photo_path),
                "detail": None,
            }
        ],
    )


def test_upload_persists_metadata_only_manifest_with_temp_object(monkeypatch):
    session = _UploadSession()
    uploaded = []

    def save_temp(*, school_uuid, upload_uuid, content, content_type):
        path = (
            f"schools/{school_uuid}/bulk-photo-imports/"
            f"{upload_uuid}/{uuid4().hex}.png"
        )
        uploaded.append((path, content, content_type))
        return path

    monkeypatch.setattr(bulk_api, "save_bulk_photo_temp", save_temp)
    before = datetime.now(timezone.utc)
    response, school, student = _upload_archive(
        monkeypatch,
        session,
        {"A-1.png": _png_bytes()},
    )

    stored = session.added[0]
    item = stored.manifest[0]
    serialized = json.dumps(stored.manifest).casefold()
    assert all(key not in serialized for key in ('"content"', "base64", "signed_url"))
    assert item == {
        "item_uuid": item["item_uuid"],
        "filename": "A-1.png",
        "admission_no": "A-1",
        "match_key": "a-1",
        "extension": ".png",
        "file_size": len(_png_bytes()),
        "status": "ready",
        "detail": None,
        "content_type": "image/png",
        "temp_storage_path": uploaded[0][0],
        "student_uuid": str(student.uuid),
        "student_name": "Asha Singh",
        "has_existing_photo": False,
        "replacement": False,
    }
    assert uploaded[0][1] == _png_bytes()
    assert uploaded[0][2] == "image/png"
    assert uploaded[0][0].startswith(
        f"schools/{school.uuid}/bulk-photo-imports/{response.manifest_uuid}/"
    )
    assert before + timedelta(hours=23, minutes=59) <= response.expires_at
    assert response.expires_at <= before + timedelta(hours=24, minutes=1)


def test_partial_temp_upload_failure_cleans_prior_objects(monkeypatch):
    session = _UploadSession()
    created = []
    deleted = []

    def save_temp(*, school_uuid, upload_uuid, content, content_type):
        if created:
            raise StorageError("storage offline")
        path = (
            f"schools/{school_uuid}/bulk-photo-imports/"
            f"{upload_uuid}/{uuid4().hex}.png"
        )
        created.append(path)
        return path

    monkeypatch.setattr(bulk_api, "save_bulk_photo_temp", save_temp)
    monkeypatch.setattr(bulk_api, "delete_storage_object", deleted.append)

    with pytest.raises(HTTPException) as exc_info:
        _upload_archive(
            monkeypatch,
            session,
            {"A-1.png": _png_bytes(), "B-2.png": _png_bytes((1, 2, 3))},
        )

    assert exc_info.value.status_code == 503
    assert deleted == created
    assert session.added == []


def test_database_failure_after_upload_cleans_temp_object(monkeypatch):
    session = _UploadSession(fail_commit=True)
    created = []
    deleted = []

    def save_temp(*, school_uuid, upload_uuid, content, content_type):
        path = (
            f"schools/{school_uuid}/bulk-photo-imports/"
            f"{upload_uuid}/{uuid4().hex}.png"
        )
        created.append(path)
        return path

    monkeypatch.setattr(bulk_api, "save_bulk_photo_temp", save_temp)
    monkeypatch.setattr(bulk_api, "delete_storage_object", deleted.append)

    with pytest.raises(RuntimeError, match="database unavailable"):
        _upload_archive(monkeypatch, session, {"A-1.png": _png_bytes()})

    assert session.rollbacks == 1
    assert deleted == created


def test_zip_paths_directories_and_invalid_inputs_are_rejected_or_flagged(monkeypatch):
    with pytest.raises(bulk_core.BulkPhotoValidationError, match="Unsafe archive path"):
        bulk_core.inspect_zip(_zip_bytes({"../A-1.png": _png_bytes()}))

    with pytest.raises(bulk_core.BulkPhotoValidationError, match="Nested archive paths"):
        bulk_core.inspect_zip(_zip_bytes({"nested/A-1.png": _png_bytes()}))

    directory_zip = io.BytesIO()
    with zipfile.ZipFile(directory_zip, "w") as archive:
        archive.writestr("nested/", b"")
    with pytest.raises(bulk_core.BulkPhotoValidationError, match="Directory entries"):
        bulk_core.inspect_zip(directory_zip.getvalue())

    unsupported = bulk_core.inspect_zip(_zip_bytes({"A-1.gif": b"gif"}))[0]
    empty = bulk_core.inspect_zip(_zip_bytes({"A-1.png": b""}))[0]
    monkeypatch.setattr(bulk_core, "MAX_IMAGE_SIZE", 1)
    oversized = bulk_core.inspect_zip(_zip_bytes({"A-1.png": _png_bytes()}))[0]
    assert unsupported["status"] == "invalid"
    assert empty["status"] == "invalid" and "empty" in empty["detail"].casefold()
    assert oversized["status"] == "invalid" and "5 mb" in oversized["detail"].casefold()


def test_preview_uses_metadata_without_downloading_or_mutating_student(monkeypatch):
    school_uuid = uuid4()
    old_photo = f"students/{uuid4()}/photo_old.png"
    student = _student(photo_path=old_photo)
    manifest = _manifest(school_uuid, student)
    session = _ManifestSession(manifest)
    monkeypatch.setattr(bulk_api, "_student_lookup", lambda *_args: {"a-1": student})
    monkeypatch.setattr(
        bulk_api,
        "download_storage_object",
        lambda _path: pytest.fail("preview must not download temp objects"),
    )

    response = bulk_api._preview_manifest(session, manifest, 20)

    assert response.ready_count == 1
    assert response.replacement_count == 1
    assert response.can_commit is True
    assert student.photo_path == old_photo
    assert all("content" not in item and "base64" not in item for item in manifest.manifest)


def test_failed_promotion_preserves_previous_photo_and_temp_object(monkeypatch):
    school_uuid = uuid4()
    student = _student(photo_path=f"students/{uuid4()}/photo_old.png")
    manifest = _manifest(school_uuid, student)
    temp_path = manifest.manifest[0]["temp_storage_path"]
    session = _ManifestSession(manifest)
    deleted = []
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20, uuid=school_uuid),
    )
    monkeypatch.setattr(bulk_api, "_student_lookup", lambda *_args: {"a-1": student})
    monkeypatch.setattr(
        bulk_api,
        "download_storage_object",
        lambda _path: (_ for _ in ()).throw(StorageError("storage offline")),
    )
    monkeypatch.setattr(bulk_api, "delete_storage_object", deleted.append)
    old_photo = student.photo_path

    response = bulk_api.commit_bulk_student_photos(
        school_uuid=school_uuid,
        manifest_uuid=manifest.uuid,
        payload=bulk_api.BulkPhotoCommitRequest(confirmed=True),
        db=session,
        current_user=SimpleNamespace(id=1),
    )

    assert response.failed_count == 1
    assert student.photo_path == old_photo
    assert deleted == []
    assert manifest.manifest[0]["temp_storage_path"] == temp_path


def test_successful_add_audits_then_cleans_consumed_temp(monkeypatch):
    school_uuid = uuid4()
    student = _student()
    manifest = _manifest(school_uuid, student)
    temp_path = manifest.manifest[0]["temp_storage_path"]
    session = _ManifestSession(manifest)
    new_url = (
        "https://example.supabase.co/storage/v1/object/public/student-photos/"
        f"students/{student.uuid}/photo_new.png"
    )
    deleted = []
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20, uuid=school_uuid),
    )
    monkeypatch.setattr(bulk_api, "_student_lookup", lambda *_args: {"a-1": student})
    monkeypatch.setattr(bulk_api, "download_storage_object", lambda _path: _png_bytes())
    monkeypatch.setattr(bulk_api, "save_student_photo", lambda *_args: new_url)
    monkeypatch.setattr(bulk_api, "delete_storage_object", deleted.append)

    response = bulk_api.commit_bulk_student_photos(
        school_uuid=school_uuid,
        manifest_uuid=manifest.uuid,
        payload=bulk_api.BulkPhotoCommitRequest(confirmed=True),
        db=session,
        current_user=SimpleNamespace(id=1),
    )

    assert response.completed is True
    assert student.photo_path == new_url
    assert session.added[0].event_type == "student_photo_added"
    assert deleted == [temp_path]
    assert manifest.manifest[0]["temp_storage_path"] is None


def test_temp_cleanup_failure_is_logged_but_commit_succeeds(monkeypatch, caplog):
    school_uuid = uuid4()
    student = _student()
    manifest = _manifest(school_uuid, student)
    session = _ManifestSession(manifest)
    new_url = (
        "https://example.supabase.co/storage/v1/object/public/student-photos/"
        f"students/{student.uuid}/photo_new.png"
    )
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20, uuid=school_uuid),
    )
    monkeypatch.setattr(bulk_api, "_student_lookup", lambda *_args: {"a-1": student})
    monkeypatch.setattr(bulk_api, "download_storage_object", lambda _path: _png_bytes())
    monkeypatch.setattr(bulk_api, "save_student_photo", lambda *_args: new_url)
    monkeypatch.setattr(
        bulk_api,
        "delete_storage_object",
        lambda _path: (_ for _ in ()).throw(StorageError("cleanup failed")),
    )

    response = bulk_api.commit_bulk_student_photos(
        school_uuid=school_uuid,
        manifest_uuid=manifest.uuid,
        payload=bulk_api.BulkPhotoCommitRequest(confirmed=True),
        db=session,
        current_user=SimpleNamespace(id=1),
    )

    assert response.completed is True
    assert response.failed_count == 0
    assert manifest.manifest[0]["temp_storage_path"] is not None
    assert "Failed to clean up consumed temp object" in caplog.text


def test_expired_import_cleanup_deletes_temp_before_record(monkeypatch):
    school_uuid = uuid4()
    student = _student()
    manifest = _manifest(
        school_uuid,
        student,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    temp_path = manifest.manifest[0]["temp_storage_path"]
    session = _ManifestSession([manifest])
    events = []
    monkeypatch.setattr(
        bulk_api,
        "delete_storage_object",
        lambda path: events.append(f"storage:{path}"),
    )
    original_delete = session.delete

    def delete_record(value):
        events.append(f"db:{value.uuid}")
        original_delete(value)

    session.delete = delete_record
    cleaned = bulk_api.cleanup_expired_bulk_photo_imports(
        session,
        school_id=20,
        school_uuid=school_uuid,
    )

    assert cleaned == 1
    assert events == [f"storage:{temp_path}", f"db:{manifest.uuid}"]


def test_preview_query_enforces_import_school_and_user_ownership(monkeypatch):
    school_uuid = uuid4()
    requested_uuid = uuid4()
    session = _ManifestSession(None)
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20, uuid=school_uuid),
    )

    with pytest.raises(HTTPException) as exc_info:
        bulk_api.preview_bulk_student_photos(
            school_uuid=school_uuid,
            manifest_uuid=requested_uuid,
            db=session,
            current_user=SimpleNamespace(id=7),
        )

    compiled = session.statement.compile()
    where_text = str(session.statement.whereclause)
    assert exc_info.value.status_code == 404
    assert "bulk_photo_imports.uuid" in where_text
    assert "bulk_photo_imports.school_id" in where_text
    assert "bulk_photo_imports.user_id" in where_text
    assert requested_uuid in compiled.params.values()
    assert 20 in compiled.params.values()
    assert 7 in compiled.params.values()


def test_expired_owned_preview_cleans_storage_and_returns_gone(monkeypatch):
    school_uuid = uuid4()
    student = _student()
    manifest = _manifest(
        school_uuid,
        student,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session = _ManifestSession(manifest)
    cleaned = []
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20, uuid=school_uuid),
    )
    monkeypatch.setattr(
        bulk_api,
        "cleanup_bulk_photo_import",
        lambda _db, item, *, school_uuid: cleaned.append((item.uuid, school_uuid)),
    )

    with pytest.raises(HTTPException) as exc_info:
        bulk_api.preview_bulk_student_photos(
            school_uuid=school_uuid,
            manifest_uuid=manifest.uuid,
            db=session,
            current_user=SimpleNamespace(id=1),
        )

    assert exc_info.value.status_code == 410
    assert cleaned == [(manifest.uuid, school_uuid)]
