import asyncio
import base64
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import UploadFile
from PIL import Image

from app.api import bulk_student_photos as bulk_api
from app.api import students as students_api
from app.core import file_storage


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _StudentSession:
    def __init__(self, student, *, fail_commit=False, events=None):
        self.student = student
        self.fail_commit = fail_commit
        self.events = events if events is not None else []
        self.rollbacks = 0

    def execute(self, _statement):
        return _Result(self.student)

    def commit(self):
        self.events.append("commit")
        if self.fail_commit:
            self.fail_commit = False
            raise RuntimeError("database unavailable")

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _student):
        self.events.append("refresh")


class _BulkSession:
    def __init__(self, manifest, *, fail_first_commit=False, events=None):
        self.manifest = manifest
        self.fail_first_commit = fail_first_commit
        self.events = events if events is not None else []
        self.commit_calls = 0
        self.rollbacks = 0

    def execute(self, _statement):
        return _Result(self.manifest)

    def commit(self):
        self.commit_calls += 1
        self.events.append("commit")
        if self.fail_first_commit and self.commit_calls == 1:
            raise RuntimeError("database unavailable")

    def rollback(self):
        self.rollbacks += 1

    def delete(self, _value):
        return None


class _StorageBucket:
    def __init__(self):
        self.uploads = []

    def upload(self, **kwargs):
        self.uploads.append(kwargs)

    def get_public_url(self, path):
        return (
            "https://example.supabase.co/storage/v1/object/public/"
            f"student-photos/{path}"
        )


class _Storage:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, bucket_name):
        assert bucket_name == file_storage.SUPABASE_BUCKET
        return self.bucket


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (3, 3), (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


def _managed_url(student_uuid, filename="photo_old.png"):
    return (
        "https://example.supabase.co/storage/v1/object/public/"
        f"student-photos/students/{student_uuid}/{filename}"
    )


def _student(photo_path=None):
    return SimpleNamespace(
        id=10,
        uuid=uuid4(),
        school_id=20,
        full_name="Asha Singh",
        photo_path=photo_path,
        is_active=True,
    )


def _patch_manual_authorization(monkeypatch):
    monkeypatch.setattr(
        students_api,
        "get_active_school",
        lambda *_args: SimpleNamespace(id=20),
    )
    monkeypatch.setattr(
        students_api,
        "require_card_data_access",
        lambda *_args: None,
    )


def _upload_manually(student, session):
    photo = UploadFile(
        filename="photo.png",
        file=io.BytesIO(_png_bytes()),
        headers={"content-type": "image/png"},
    )
    return asyncio.run(
        students_api.upload_student_photo(
            school_uuid=uuid4(),
            student_uuid=student.uuid,
            photo=photo,
            db=session,
            current_user=SimpleNamespace(id=1),
        )
    )


def _bulk_context(student):
    content = base64.b64encode(_png_bytes()).decode("ascii")
    manifest = SimpleNamespace(
        uuid=uuid4(),
        school_id=20,
        user_id=1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status="pending",
        total_files=1,
        manifest=[
            {
                "filename": "A-1.png",
                "admission_no": "A-1",
                "content": content,
                "content_type": "image/png",
                "status": "ready",
            }
        ],
    )
    return manifest


def _commit_bulk(monkeypatch, student, session):
    monkeypatch.setattr(
        bulk_api,
        "_authorize",
        lambda *_args: SimpleNamespace(id=20),
    )
    monkeypatch.setattr(
        bulk_api,
        "_student_lookup",
        lambda *_args: {"a-1": student},
    )
    return bulk_api.commit_bulk_student_photos(
        school_uuid=uuid4(),
        manifest_uuid=session.manifest.uuid,
        payload=bulk_api.BulkPhotoCommitRequest(confirmed=True),
        db=session,
        current_user=SimpleNamespace(id=1),
    )


def test_student_photo_uploads_use_unique_versioned_non_overwrite_paths(monkeypatch):
    bucket = _StorageBucket()
    monkeypatch.setattr(
        file_storage,
        "supabase",
        SimpleNamespace(storage=_Storage(bucket)),
    )
    student_uuid = uuid4()

    first = file_storage.save_student_photo(student_uuid, _png_bytes(), "image/png")
    second = file_storage.save_student_photo(student_uuid, _png_bytes(), "image/png")

    first_upload, second_upload = bucket.uploads
    assert first_upload["path"] != second_upload["path"]
    assert first_upload["path"].startswith(f"students/{student_uuid}/photo_")
    assert first_upload["path"].endswith(".png")
    assert first_upload["file_options"]["upsert"] == "false"
    assert second_upload["file_options"]["upsert"] == "false"
    assert first != second


@pytest.mark.parametrize(
    "content_type,extension",
    [("image/jpeg", ".jpg"), ("image/png", ".png"), ("image/webp", ".webp")],
)
def test_student_photo_upload_preserves_validated_extension(
    monkeypatch,
    content_type,
    extension,
):
    bucket = _StorageBucket()
    monkeypatch.setattr(
        file_storage,
        "supabase",
        SimpleNamespace(storage=_Storage(bucket)),
    )
    image_format = file_storage.ALLOWED_IMAGE_FORMATS[content_type]
    output = io.BytesIO()
    Image.new("RGB", (3, 3), (20, 80, 140)).save(output, format=image_format)

    file_storage.save_student_photo(uuid4(), output.getvalue(), content_type)

    assert bucket.uploads[0]["path"].endswith(extension)


def test_managed_student_photo_path_accepts_current_and_legacy_references():
    student_uuid = uuid4()
    object_path = f"students/{student_uuid}/photo_old.png"

    assert file_storage.managed_student_photo_storage_path(
        object_path,
        student_uuid,
    ) == object_path
    assert file_storage.managed_student_photo_storage_path(
        _managed_url(student_uuid, "photo.png"),
        student_uuid,
    ) == f"students/{student_uuid}/photo.png"


def test_managed_student_photo_path_requires_the_expected_student():
    student_uuid = uuid4()

    assert file_storage.managed_student_photo_storage_path(
        _managed_url(student_uuid),
        uuid4(),
    ) is None


@pytest.mark.parametrize(
    "reference",
    [
        "https://cdn.example.test/students/photo.png",
        "https://example.supabase.co/storage/v1/object/public/other-bucket/students/x/photo.png",
        (
            "https://example.supabase.co/storage/v1/object/public/"
            "student-photos/students/x/photo.png?token=unsafe"
        ),
        "students/not-a-uuid/photo.png",
        f"students/{uuid4()}/avatar.png",
        "/students/unsafe/photo.png",
    ],
)
def test_managed_student_photo_path_rejects_external_or_unknown_references(reference):
    assert file_storage.managed_student_photo_storage_path(reference) is None


def test_existing_student_photo_validation_still_rejects_oversize_and_invalid_images():
    with pytest.raises(ValueError, match="5 MB"):
        file_storage.validate_student_photo(
            b"x" * (file_storage.MAX_STUDENT_PHOTO_SIZE + 1),
            "image/png",
        )
    with pytest.raises(ValueError, match="not a valid image"):
        file_storage.validate_student_photo(b"not-an-image", "image/png")


def test_manual_replacement_commits_new_url_before_deleting_old(monkeypatch):
    _patch_manual_authorization(monkeypatch)
    student = _student()
    old_url = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    student.photo_path = old_url
    events = []
    session = _StudentSession(student, events=events)
    monkeypatch.setattr(students_api, "save_student_photo", lambda **_kwargs: new_url)
    monkeypatch.setattr(
        students_api,
        "delete_storage_object",
        lambda path: events.append(f"delete:{path}"),
    )

    result = _upload_manually(student, session)

    assert result.photo_path == new_url
    assert events == [
        "commit",
        "refresh",
        f"delete:students/{student.uuid}/photo_old.png",
    ]


def test_manual_upload_failure_preserves_old_photo_without_deletion(monkeypatch):
    _patch_manual_authorization(monkeypatch)
    student = _student()
    old_url = _managed_url(student.uuid)
    student.photo_path = old_url
    session = _StudentSession(student)
    deleted = []
    monkeypatch.setattr(
        students_api,
        "save_student_photo",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("storage offline")),
    )
    monkeypatch.setattr(students_api, "delete_storage_object", deleted.append)

    with pytest.raises(Exception) as exc_info:
        _upload_manually(student, session)

    assert getattr(exc_info.value, "status_code", None) == 422
    assert student.photo_path == old_url
    assert deleted == []


def test_manual_database_failure_keeps_old_and_cleans_new_upload(monkeypatch):
    _patch_manual_authorization(monkeypatch)
    student = _student()
    old_url = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    student.photo_path = old_url
    session = _StudentSession(student, fail_commit=True)
    deleted = []
    monkeypatch.setattr(students_api, "save_student_photo", lambda **_kwargs: new_url)
    monkeypatch.setattr(students_api, "delete_storage_object", deleted.append)

    with pytest.raises(RuntimeError, match="database unavailable"):
        _upload_manually(student, session)

    assert student.photo_path == old_url
    assert session.rollbacks == 1
    assert deleted == [f"students/{student.uuid}/photo_new.png"]


def test_manual_cleanup_failure_does_not_fail_replacement(monkeypatch):
    _patch_manual_authorization(monkeypatch)
    student = _student()
    student.photo_path = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    session = _StudentSession(student)
    monkeypatch.setattr(students_api, "save_student_photo", lambda **_kwargs: new_url)
    monkeypatch.setattr(
        students_api,
        "delete_storage_object",
        lambda _path: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    result = _upload_manually(student, session)

    assert result.photo_path == new_url


def test_manual_external_previous_url_is_not_deleted(monkeypatch):
    _patch_manual_authorization(monkeypatch)
    student = _student("https://external.example.test/legacy.png")
    new_url = _managed_url(student.uuid, "photo_new.png")
    session = _StudentSession(student)
    deleted = []
    monkeypatch.setattr(students_api, "save_student_photo", lambda **_kwargs: new_url)
    monkeypatch.setattr(students_api, "delete_storage_object", deleted.append)

    _upload_manually(student, session)

    assert deleted == []


def test_bulk_replacement_commits_before_old_photo_cleanup(monkeypatch):
    student = _student()
    student.photo_path = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    manifest = _bulk_context(student)
    events = []
    session = _BulkSession(manifest, events=events)
    monkeypatch.setattr(bulk_api, "save_student_photo", lambda *_args: new_url)
    monkeypatch.setattr(
        bulk_api,
        "delete_storage_object",
        lambda path: events.append(f"delete:{path}"),
    )

    response = _commit_bulk(monkeypatch, student, session)

    assert response.uploaded_count == 1
    assert response.replacement_count == 1
    assert response.failed_count == 0
    assert student.photo_path == new_url
    assert events[:2] == [
        "commit",
        f"delete:students/{student.uuid}/photo_old.png",
    ]


def test_bulk_database_failure_keeps_old_and_cleans_new_upload(monkeypatch):
    student = _student()
    old_url = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    student.photo_path = old_url
    manifest = _bulk_context(student)
    session = _BulkSession(manifest, fail_first_commit=True)
    deleted = []
    monkeypatch.setattr(bulk_api, "save_student_photo", lambda *_args: new_url)
    monkeypatch.setattr(bulk_api, "delete_storage_object", deleted.append)

    response = _commit_bulk(monkeypatch, student, session)

    assert response.uploaded_count == 0
    assert response.failed_count == 1
    assert student.photo_path == old_url
    assert session.rollbacks == 1
    assert deleted == [f"students/{student.uuid}/photo_new.png"]


def test_bulk_cleanup_failure_still_reports_success(monkeypatch):
    student = _student()
    student.photo_path = _managed_url(student.uuid)
    new_url = _managed_url(student.uuid, "photo_new.png")
    manifest = _bulk_context(student)
    session = _BulkSession(manifest)
    monkeypatch.setattr(bulk_api, "save_student_photo", lambda *_args: new_url)
    monkeypatch.setattr(
        bulk_api,
        "delete_storage_object",
        lambda _path: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    response = _commit_bulk(monkeypatch, student, session)

    assert response.uploaded_count == 1
    assert response.failed_count == 0
    assert response.completed is True
