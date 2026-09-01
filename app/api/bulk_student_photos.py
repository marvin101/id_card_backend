from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.bulk_student_photos import (
    BulkPhotoValidationError,
    inspect_zip,
)
from app.core.database import get_db
from app.core.file_storage import (
    StorageError,
    delete_storage_object,
    download_storage_object,
    managed_bulk_photo_temp_storage_path,
    managed_student_photo_storage_path,
    save_bulk_photo_temp,
    save_student_photo,
)
from app.core.school_access import (
    get_active_school,
    require_card_data_access,
)
from app.core.security import get_current_user
from app.core.student_audit import record_student_audit
from app.models.bulk_photo_import import BulkPhotoImport
from app.models.student import Student
from app.models.users import User


router = APIRouter(
    prefix="/schools/{school_uuid}/student-photos/bulk",
    tags=["Bulk Student Photos"],
)


MANIFEST_TTL_HOURS = 24

logger = logging.getLogger(__name__)


# ==========================================================
# Response models
# ==========================================================


class BulkPhotoItem(BaseModel):
    filename: str
    admission_no: str

    student_uuid: UUID | None = None
    student_name: str | None = None

    status: str

    detail: str | None = None

    has_existing_photo: bool = False


class BulkPhotoUploadResponse(BaseModel):
    manifest_uuid: UUID
    filename: str
    total_files: int
    expires_at: datetime


class BulkPhotoPreviewResponse(BaseModel):
    manifest_uuid: UUID

    total_files: int
    ready_count: int
    unmatched_count: int
    invalid_count: int
    replacement_count: int

    can_commit: bool

    items: list[BulkPhotoItem]


class BulkPhotoCommitRequest(BaseModel):
    confirmed: bool = Field(default=False)


class BulkPhotoCommitItem(BaseModel):
    filename: str
    admission_no: str

    student_uuid: UUID | None = None
    student_name: str | None = None

    status: str
    detail: str | None = None


class BulkPhotoCommitResponse(BaseModel):
    manifest_uuid: UUID

    total_files: int

    uploaded_count: int
    failed_count: int
    unmatched_count: int
    invalid_count: int

    replacement_count: int

    completed: bool

    items: list[BulkPhotoCommitItem]


# ==========================================================
# Helpers
# ==========================================================


def _authorize(
    db: Session,
    current_user: User,
    school_uuid: UUID,
):
    school = get_active_school(
        db,
        school_uuid,
    )

    require_card_data_access(
        db,
        current_user,
        school.id,
        (
            "Only a school administrator or card "
            "operator can upload student photos"
        ),
    )

    return school


def _student_lookup(
    db: Session,
    school_id: int,
) -> dict[str, Student]:

    students = db.execute(
        select(Student).where(
            Student.school_id == school_id,
            Student.is_active.is_(True),
        )
    ).scalars().all()

    return {
        student.admission_no.strip().casefold(): student
        for student in students
    }


def _mime_type_from_extension(
    extension: str,
) -> str:

    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[extension]


def _resolved_manifest_entries(
    entries: list[dict[str, Any]],
    students: dict[str, Student],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []

    for original in entries:
        item = dict(original)
        if item.get("status") in {"invalid", "uploaded"}:
            resolved.append(item)
            continue

        admission_no = item.get("admission_no", "").strip()
        student = students.get(admission_no.casefold())
        if student is None:
            item.update(
                status="unmatched",
                student_uuid=None,
                student_name=None,
                has_existing_photo=False,
                replacement=False,
                detail="Student was not found in the selected school.",
            )
        else:
            has_existing_photo = bool(student.photo_path)
            item.update(
                status="ready",
                student_uuid=str(student.uuid),
                student_name=student.full_name,
                has_existing_photo=has_existing_photo,
                replacement=has_existing_photo,
                detail=None,
            )
        resolved.append(item)

    return resolved


def _safe_delete_paths(paths: list[str], *, context: str) -> bool:
    succeeded = True
    for storage_path in paths:
        try:
            delete_storage_object(storage_path)
        except Exception:
            succeeded = False
            logger.warning(
                "Failed to clean up %s object %s",
                context,
                storage_path,
                exc_info=True,
            )
    return succeeded


def _temp_paths_for_import(
    manifest: BulkPhotoImport,
    *,
    school_uuid: UUID,
) -> tuple[list[str], bool]:
    paths: list[str] = []
    all_paths_safe = True
    manifest_entries = [dict(item) for item in manifest.manifest]

    for item in manifest_entries:
        candidate = item.get("temp_storage_path")
        if not candidate:
            continue
        safe_path = managed_bulk_photo_temp_storage_path(
            candidate,
            school_uuid=school_uuid,
            upload_uuid=manifest.uuid,
        )
        if safe_path is None:
            all_paths_safe = False
            logger.warning(
                "Refusing unsafe bulk-photo temp path for import %s",
                manifest.uuid,
            )
            continue
        paths.append(safe_path)
    return paths, all_paths_safe


def cleanup_bulk_photo_import(
    db: Session,
    manifest: BulkPhotoImport,
    *,
    school_uuid: UUID,
) -> bool:
    """Delete an import only after its known temporary objects are removed."""
    paths, all_paths_safe = _temp_paths_for_import(
        manifest,
        school_uuid=school_uuid,
    )
    if not all_paths_safe or not _safe_delete_paths(paths, context="expired temp"):
        return False

    try:
        db.delete(manifest)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Failed to delete expired bulk-photo import %s",
            manifest.uuid,
            exc_info=True,
        )
        return False
    return True


def cleanup_expired_bulk_photo_imports(
    db: Session,
    *,
    school_id: int,
    school_uuid: UUID,
    now: datetime | None = None,
) -> int:
    """On-access cleanup for abandoned imports; no scheduler is required."""
    expired = db.execute(
        select(BulkPhotoImport).where(
            BulkPhotoImport.school_id == school_id,
            BulkPhotoImport.expires_at <= (now or datetime.now(timezone.utc)),
        )
    ).scalars().all()
    return sum(
        cleanup_bulk_photo_import(db, item, school_uuid=school_uuid)
        for item in expired
    )


def _preview_manifest(
    db: Session,
    manifest: BulkPhotoImport,
    school_id: int,
) -> BulkPhotoPreviewResponse:
    resolved = _resolved_manifest_entries(
        manifest.manifest,
        _student_lookup(db, school_id),
    )
    items = [
        BulkPhotoItem(
            filename=item.get("filename", ""),
            admission_no=item.get("admission_no", "").strip(),
            student_uuid=item.get("student_uuid"),
            student_name=item.get("student_name"),
            status=item.get("status", "invalid"),
            detail=item.get("detail"),
            has_existing_photo=bool(item.get("has_existing_photo")),
        )
        for item in resolved
    ]
    ready_count = sum(item.status == "ready" for item in items)
    unmatched_count = sum(item.status == "unmatched" for item in items)
    invalid_count = sum(item.status == "invalid" for item in items)
    replacement_count = sum(
        item.status == "ready" and item.has_existing_photo
        for item in items
    )

    manifest.manifest = resolved
    manifest.status = "previewed"
    db.commit()

    return BulkPhotoPreviewResponse(
        manifest_uuid=manifest.uuid,
        total_files=manifest.total_files,
        ready_count=ready_count,
        unmatched_count=unmatched_count,
        invalid_count=invalid_count,
        replacement_count=replacement_count,
        can_commit=(
            ready_count > 0
            and unmatched_count == 0
            and invalid_count == 0
        ),
        items=items,
    )


# ==========================================================
# Upload
# ==========================================================


@router.post(
    "/upload",
    response_model=BulkPhotoUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_bulk_student_photos(
    school_uuid: UUID,
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(
        db,
        current_user,
        school_uuid,
    )

    try:
        cleanup_expired_bulk_photo_imports(
            db,
            school_id=school.id,
            school_uuid=school.uuid,
        )
    except Exception:
        db.rollback()
        logger.warning(
            "On-access bulk-photo cleanup failed for school %s",
            school.uuid,
            exc_info=True,
        )

    if archive.content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please upload a ZIP archive.",
        )

    content = await archive.read()

    try:
        entries = inspect_zip(content)

    except BulkPhotoValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    upload_uuid = uuid4()
    manifest_entries: list[dict[str, Any]] = []
    uploaded_temp_paths: list[str] = []

    try:
        for entry in entries:
            admission_no = entry["admission_no"]
            item = {
                "item_uuid": str(uuid4()),
                "filename": entry["filename"],
                "admission_no": admission_no,
                "match_key": admission_no.strip().casefold(),
                "extension": entry.get("extension"),
                "file_size": entry.get("file_size", 0),
                "status": entry["status"],
                "detail": entry.get("detail"),
            }

            if entry.get("status") == "pending":
                content_type = _mime_type_from_extension(entry["extension"])
                temp_storage_path = save_bulk_photo_temp(
                    school_uuid=school.uuid,
                    upload_uuid=upload_uuid,
                    content=entry["content"],
                    content_type=content_type,
                )
                uploaded_temp_paths.append(temp_storage_path)
                item.update(
                    content_type=content_type,
                    temp_storage_path=temp_storage_path,
                )

            manifest_entries.append(item)

        manifest_entries = _resolved_manifest_entries(
            manifest_entries,
            _student_lookup(db, school.id),
        )

    except Exception as exc:
        _safe_delete_paths(uploaded_temp_paths, context="partial temp upload")
        if isinstance(exc, (StorageError, ValueError)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Temporary photo storage is currently unavailable.",
            ) from exc
        raise

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(hours=MANIFEST_TTL_HOURS)
    )

    bulk_import = BulkPhotoImport(
        uuid=upload_uuid,
        school_id=school.id,
        user_id=current_user.id,
        manifest=manifest_entries,
        status="uploaded",
        total_files=len(manifest_entries),
        expires_at=expires_at,
    )

    try:
        db.add(bulk_import)
        db.commit()
        db.refresh(bulk_import)
    except Exception:
        db.rollback()
        _safe_delete_paths(uploaded_temp_paths, context="unpersisted temp upload")
        raise

    return BulkPhotoUploadResponse(
        manifest_uuid=bulk_import.uuid,
        filename=archive.filename or "student_photos.zip",
        total_files=bulk_import.total_files,
        expires_at=bulk_import.expires_at,
    )


# ==========================================================
# Preview
# ==========================================================


@router.post(
    "/{manifest_uuid}/preview",
    response_model=BulkPhotoPreviewResponse,
)
def preview_bulk_student_photos(
    school_uuid: UUID,
    manifest_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(
        db,
        current_user,
        school_uuid,
    )

    manifest = db.execute(
        select(BulkPhotoImport).where(
            BulkPhotoImport.uuid == manifest_uuid,
            BulkPhotoImport.school_id == school.id,
            BulkPhotoImport.user_id == current_user.id,
        )
    ).scalar_one_or_none()

    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bulk photo upload was not found.",
        )

    if manifest.expires_at <= datetime.now(
        timezone.utc
    ):
        cleanup_bulk_photo_import(
            db,
            manifest,
            school_uuid=school.uuid,
        )

        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Bulk photo upload has expired.",
        )

    if manifest.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bulk photo upload has already been completed.",
        )

    return _preview_manifest(
        db,
        manifest,
        school.id,
    )


# ==========================================================
# Commit
# ==========================================================


@router.post(
    "/{manifest_uuid}/commit",
    response_model=BulkPhotoCommitResponse,
)
def commit_bulk_student_photos(
    school_uuid: UUID,
    manifest_uuid: UUID,
    payload: BulkPhotoCommitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit confirmation is required.",
        )

    school = _authorize(
        db,
        current_user,
        school_uuid,
    )

    manifest = db.execute(
        select(BulkPhotoImport).where(
            BulkPhotoImport.uuid == manifest_uuid,
            BulkPhotoImport.school_id == school.id,
            BulkPhotoImport.user_id == current_user.id,
        )
    ).scalar_one_or_none()

    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bulk photo upload was not found.",
        )

    if manifest.expires_at <= datetime.now(
        timezone.utc
    ):
        cleanup_bulk_photo_import(
            db,
            manifest,
            school_uuid=school.uuid,
        )

        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Bulk photo upload has expired.",
        )

    if manifest.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bulk photo upload has already been completed.",
        )

    students = _student_lookup(
        db,
        school.id,
    )

    results: list[BulkPhotoCommitItem] = []

    uploaded_count = 0
    failed_count = 0
    unmatched_count = 0
    invalid_count = 0
    replacement_count = 0

    all_processed = True

    manifest_entries = [dict(item) for item in manifest.manifest]

    for item in manifest_entries:

        filename = item.get("filename", "")
        admission_no = item.get("admission_no", "")

        if item.get("status") == "invalid":

            invalid_count += 1
            all_processed = False

            results.append(
                BulkPhotoCommitItem(
                    filename=filename,
                    admission_no=admission_no,
                    status="invalid",
                    detail=item.get("detail"),
                )
            )

            continue

        if item.get("status") == "uploaded":
            uploaded_count += 1
            replacement_count += int(bool(item.get("replacement")))
            results.append(
                BulkPhotoCommitItem(
                    filename=filename,
                    admission_no=admission_no,
                    student_uuid=item.get("student_uuid"),
                    student_name=item.get("student_name"),
                    status="uploaded",
                    detail="Photo was already uploaded.",
                )
            )
            continue

        student = students.get(
            admission_no.strip().casefold()
        )

        if student is None:

            unmatched_count += 1
            all_processed = False

            results.append(
                BulkPhotoCommitItem(
                    filename=filename,
                    admission_no=admission_no,
                    status="unmatched",
                    detail=(
                        "Student was not found in "
                        "the selected school."
                    ),
                )
            )

            continue

        temp_storage_path = managed_bulk_photo_temp_storage_path(
            item.get("temp_storage_path"),
            school_uuid=school.uuid,
            upload_uuid=manifest.uuid,
        )

        if temp_storage_path is None:

            failed_count += 1
            all_processed = False

            results.append(
                BulkPhotoCommitItem(
                    filename=filename,
                    admission_no=admission_no,
                    student_uuid=student.uuid,
                    student_name=student.full_name,
                    status="failed",
                    detail=(
                        "Temporary photo object is missing or invalid."
                    ),
                )
            )

            continue

        previous_photo_path = student.photo_path
        had_existing_photo = bool(previous_photo_path)
        public_url: str | None = None
        previous_item = dict(item)

        try:
            content = download_storage_object(temp_storage_path)
            public_url = save_student_photo(
                student.uuid,
                content,
                item.get("content_type"),
            )

            student.photo_path = public_url

            record_student_audit(
                db,
                student=student,
                actor=current_user,
                event_type="student_photo_replaced" if had_existing_photo else "student_photo_added",
                field_name="photo_path",
                old_value=previous_photo_path,
                new_value=public_url,
                note="Bulk photo import",
            )

            item.update(
                status="uploaded",
                detail="Photo uploaded successfully.",
                student_uuid=str(student.uuid),
                student_name=student.full_name,
                has_existing_photo=had_existing_photo,
                replacement=had_existing_photo,
            )
            manifest.manifest = manifest_entries
            db.commit()

        except Exception as exc:

            db.rollback()
            student.photo_path = previous_photo_path
            item.clear()
            item.update(previous_item)
            manifest.manifest = manifest_entries

            new_storage_path = managed_student_photo_storage_path(
                public_url,
                student.uuid,
            )
            if new_storage_path is not None:
                try:
                    delete_storage_object(new_storage_path)
                except Exception:
                    logger.warning(
                        "Failed to clean up newly orphaned student photo %s",
                        new_storage_path,
                        exc_info=True,
                    )

            failed_count += 1
            all_processed = False

            results.append(
                BulkPhotoCommitItem(
                    filename=filename,
                    admission_no=admission_no,
                    student_uuid=student.uuid,
                    student_name=student.full_name,
                    status="failed",
                    detail=str(exc),
                )
            )

            continue

        uploaded_count += 1

        if had_existing_photo:
            replacement_count += 1

        results.append(
            BulkPhotoCommitItem(
                filename=filename,
                admission_no=admission_no,
                student_uuid=student.uuid,
                student_name=student.full_name,
                status="uploaded",
                detail="Photo uploaded successfully.",
            )
        )

        previous_storage_path = managed_student_photo_storage_path(
            previous_photo_path,
            student.uuid,
        )
        if previous_storage_path is not None:
            try:
                delete_storage_object(previous_storage_path)
            except Exception:
                logger.warning(
                    "Failed to clean up replaced student photo %s",
                    previous_storage_path,
                    exc_info=True,
                )

        if _safe_delete_paths([temp_storage_path], context="consumed temp"):
            item["temp_storage_path"] = None

    manifest.manifest = manifest_entries
    if all_processed:
        manifest.status = "completed"
        db.commit()

    else:
        manifest.status = "partial"
        db.commit()

    return BulkPhotoCommitResponse(
        manifest_uuid=manifest.uuid,
        total_files=manifest.total_files,
        uploaded_count=uploaded_count,
        failed_count=failed_count,
        unmatched_count=unmatched_count,
        invalid_count=invalid_count,
        replacement_count=replacement_count,
        completed=all_processed,
        items=results,
    )
