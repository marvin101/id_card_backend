from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

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
    delete_storage_object,
    managed_student_photo_storage_path,
    save_student_photo,
)
from app.core.school_access import (
    get_active_school,
    require_card_data_access,
)
from app.core.security import get_current_user
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


def _preview_manifest(
    db: Session,
    manifest: BulkPhotoImport,
    school_id: int,
) -> BulkPhotoPreviewResponse:

    students = _student_lookup(
        db,
        school_id,
    )

    items: list[BulkPhotoItem] = []

    ready_count = 0
    unmatched_count = 0
    invalid_count = 0
    replacement_count = 0

    changed = False

    for item in manifest.manifest:

        filename = item.get("filename", "")
        admission_no = item.get("admission_no", "").strip()

        current_status = item.get("status")

        if current_status == "invalid":
            invalid_count += 1

            items.append(
                BulkPhotoItem(
                    filename=filename,
                    admission_no=admission_no,
                    status="invalid",
                    detail=item.get("detail"),
                )
            )

            continue

        student = students.get(
            admission_no.casefold()
        )

        if student is None:

            item["status"] = "unmatched"
            item["student_uuid"] = None
            item["student_name"] = None
            item["has_existing_photo"] = False

            changed = True

            unmatched_count += 1

            items.append(
                BulkPhotoItem(
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

        has_existing_photo = bool(
            student.photo_path
        )

        item["status"] = "ready"
        item["student_uuid"] = str(
            student.uuid
        )
        item["student_name"] = student.full_name
        item["has_existing_photo"] = (
            has_existing_photo
        )

        changed = True

        ready_count += 1

        if has_existing_photo:
            replacement_count += 1

        items.append(
            BulkPhotoItem(
                filename=filename,
                admission_no=admission_no,
                student_uuid=student.uuid,
                student_name=student.full_name,
                status="ready",
                has_existing_photo=has_existing_photo,
            )
        )

    if changed:
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

    manifest_entries: list[dict[str, Any]] = []

    for entry in entries:

        item = {
            "filename": entry["filename"],
            "admission_no": entry["admission_no"],
            "status": entry["status"],
            "detail": entry.get("detail"),
        }

        if entry.get("status") == "pending":

            raw_content = entry["content"]

            item["content"] = (
                base64.b64encode(raw_content)
                .decode("ascii")
            )

            item["content_type"] = (
                _mime_type_from_extension(
                    entry["extension"]
                )
            )

        manifest_entries.append(item)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(hours=MANIFEST_TTL_HOURS)
    )

    bulk_import = BulkPhotoImport(
        school_id=school.id,
        user_id=current_user.id,
        manifest=manifest_entries,
        status="uploaded",
        total_files=len(manifest_entries),
        expires_at=expires_at,
    )

    db.add(bulk_import)
    db.commit()
    db.refresh(bulk_import)

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
        db.delete(manifest)
        db.commit()

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
        db.delete(manifest)
        db.commit()

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

    for item in manifest.manifest:

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

        encoded_content = item.get("content")

        if not encoded_content:

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
                        "Temporary image data is missing."
                    ),
                )
            )

            continue

        try:
            content = base64.b64decode(
                encoded_content,
                validate=True,
            )

        except Exception:

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
                        "Temporary image data is invalid."
                    ),
                )
            )

            continue

        previous_photo_path = student.photo_path
        had_existing_photo = bool(previous_photo_path)
        public_url: str | None = None

        try:
            public_url = save_student_photo(
                student.uuid,
                content,
                item.get("content_type"),
            )

            student.photo_path = public_url

            db.commit()

        except Exception as exc:

            db.rollback()
            student.photo_path = previous_photo_path

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

        item["status"] = "uploaded"

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
