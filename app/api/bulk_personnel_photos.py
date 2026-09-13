from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.bulk_student_photos import (
    MANIFEST_TTL_HOURS,
    _mime_type_from_extension,
    _safe_delete_paths,
    cleanup_bulk_photo_import,
    cleanup_expired_bulk_photo_imports,
)
from app.core.bulk_student_photos import BulkPhotoValidationError, MAX_ZIP_SIZE, inspect_zip
from app.core.database import get_db
from app.core.file_storage import (
    StorageError,
    delete_storage_object,
    download_storage_object,
    managed_bulk_photo_temp_storage_path,
    managed_personnel_photo_storage_path,
    save_bulk_photo_temp,
    save_personnel_photo,
)
from app.core.personnel_audit import record_personnel_audit
from app.core.school_access import get_active_school, require_identity_data_access
from app.core.security import get_current_user
from app.models.bulk_photo_import import BulkPhotoImport
from app.models.personnel import Personnel
from app.models.users import User
from app.schemas.personnel import PersonnelType


router = APIRouter(
    prefix="/schools/{school_uuid}/personnel-photos/bulk",
    tags=["Bulk Personnel Photos"],
)
logger = logging.getLogger(__name__)


class PersonnelBulkPhotoItem(BaseModel):
    filename: str
    employee_no: str
    personnel_uuid: UUID | None = None
    personnel_name: str | None = None
    status: str
    detail: str | None = None
    has_existing_photo: bool = False


class PersonnelBulkPhotoUploadResponse(BaseModel):
    manifest_uuid: UUID
    filename: str
    personnel_type: PersonnelType
    total_files: int
    expires_at: datetime


class PersonnelBulkPhotoPreviewResponse(BaseModel):
    manifest_uuid: UUID
    personnel_type: PersonnelType
    total_files: int
    ready_count: int
    unmatched_count: int
    invalid_count: int
    replacement_count: int
    can_commit: bool
    items: list[PersonnelBulkPhotoItem]


class PersonnelBulkPhotoCommitRequest(BaseModel):
    confirmed: bool = Field(default=False)


class PersonnelBulkPhotoCommitItem(BaseModel):
    filename: str
    employee_no: str
    personnel_uuid: UUID | None = None
    personnel_name: str | None = None
    status: str
    detail: str | None = None


class PersonnelBulkPhotoCommitResponse(BaseModel):
    manifest_uuid: UUID
    personnel_type: PersonnelType
    total_files: int
    uploaded_count: int
    failed_count: int
    unmatched_count: int
    invalid_count: int
    replacement_count: int
    completed: bool
    items: list[PersonnelBulkPhotoCommitItem]


def _authorize(db: Session, current_user: User, school_uuid: UUID):
    school = get_active_school(db, school_uuid)
    require_identity_data_access(
        db,
        current_user,
        school.id,
        "Only a school administrator or card operator can upload personnel photos",
    )
    return school


def _personnel_lookup(
    db: Session,
    school_id: int,
    personnel_type: PersonnelType,
) -> dict[str, Personnel]:
    rows = db.execute(
        select(Personnel).where(
            Personnel.school_id == school_id,
            Personnel.personnel_type == personnel_type.value,
            Personnel.is_active.is_(True),
        )
    ).scalars().all()
    lookup: dict[str, Personnel] = {}
    for item in rows:
        key = item.employee_no.strip().casefold()
        if key in lookup:
            raise HTTPException(
                status_code=409,
                detail="Ambiguous employee numbers exist in this school; correct them before importing photos.",
            )
        lookup[key] = item
    return lookup


def _resolved_entries(
    entries: list[dict[str, Any]],
    personnel: dict[str, Personnel],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for original in entries:
        item = dict(original)
        if item.get("status") in {"invalid", "uploaded"}:
            resolved.append(item)
            continue
        employee_no = item.get("employee_no", "").strip()
        record = personnel.get(employee_no.casefold())
        if record is None:
            item.update(
                status="unmatched",
                personnel_uuid=None,
                personnel_name=None,
                has_existing_photo=False,
                replacement=False,
                detail="Personnel was not found in the selected school and type.",
            )
        else:
            has_existing_photo = bool(record.photo_path)
            item.update(
                status="ready",
                personnel_uuid=str(record.uuid),
                personnel_name=record.full_name,
                has_existing_photo=has_existing_photo,
                replacement=has_existing_photo,
                detail=None,
            )
        resolved.append(item)
    return resolved


def _load_manifest(
    db: Session,
    *,
    manifest_uuid: UUID,
    school_id: int,
    user_id: int,
    personnel_type: PersonnelType,
) -> BulkPhotoImport:
    manifest = db.execute(
        select(BulkPhotoImport).where(
            BulkPhotoImport.uuid == manifest_uuid,
            BulkPhotoImport.school_id == school_id,
            BulkPhotoImport.user_id == user_id,
        )
    ).scalar_one_or_none()
    if manifest is None or any(
        item.get("personnel_type") != personnel_type.value
        for item in manifest.manifest
    ):
        raise HTTPException(status_code=404, detail="Bulk personnel photo upload was not found.")
    return manifest


def _ensure_usable(
    db: Session,
    manifest: BulkPhotoImport,
    *,
    school_uuid: UUID,
) -> None:
    if manifest.expires_at <= datetime.now(timezone.utc):
        cleanup_bulk_photo_import(db, manifest, school_uuid=school_uuid)
        raise HTTPException(status_code=410, detail="Bulk personnel photo upload has expired.")
    if manifest.status == "completed":
        raise HTTPException(status_code=409, detail="Bulk personnel photo upload has already been completed.")


def _preview(
    db: Session,
    manifest: BulkPhotoImport,
    school_id: int,
    personnel_type: PersonnelType,
) -> PersonnelBulkPhotoPreviewResponse:
    resolved = _resolved_entries(
        manifest.manifest,
        _personnel_lookup(db, school_id, personnel_type),
    )
    items = [
        PersonnelBulkPhotoItem(
            filename=item.get("filename", ""),
            employee_no=item.get("employee_no", "").strip(),
            personnel_uuid=item.get("personnel_uuid"),
            personnel_name=item.get("personnel_name"),
            status=item.get("status", "invalid"),
            detail=item.get("detail"),
            has_existing_photo=bool(item.get("has_existing_photo")),
        )
        for item in resolved
    ]
    ready = sum(item.status == "ready" for item in items)
    unmatched = sum(item.status == "unmatched" for item in items)
    invalid = sum(item.status == "invalid" for item in items)
    replacements = sum(item.status == "ready" and item.has_existing_photo for item in items)
    manifest.manifest = resolved
    manifest.status = "previewed"
    db.commit()
    return PersonnelBulkPhotoPreviewResponse(
        manifest_uuid=manifest.uuid,
        personnel_type=personnel_type,
        total_files=manifest.total_files,
        ready_count=ready,
        unmatched_count=unmatched,
        invalid_count=invalid,
        replacement_count=replacements,
        can_commit=ready > 0 and unmatched == 0 and invalid == 0,
        items=items,
    )


@router.post("/upload", response_model=PersonnelBulkPhotoUploadResponse, status_code=201)
async def upload_bulk_personnel_photos(
    school_uuid: UUID,
    personnel_type: PersonnelType = Query(...),
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    try:
        cleanup_expired_bulk_photo_imports(
            db, school_id=school.id, school_uuid=school.uuid
        )
    except Exception:
        db.rollback()
        logger.warning("On-access photo cleanup failed for school %s", school.uuid, exc_info=True)
    if archive.content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(status_code=422, detail="Please upload a ZIP archive.")
    content = await archive.read(MAX_ZIP_SIZE + 1)
    try:
        entries = inspect_zip(
            content,
            identifier_key="employee_no",
            identifier_label="employee number",
        )
    except BulkPhotoValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    upload_uuid = uuid4()
    manifest_entries: list[dict[str, Any]] = []
    uploaded_temp_paths: list[str] = []
    try:
        for entry in entries:
            employee_no = entry["employee_no"]
            item = {
                "item_uuid": str(uuid4()),
                "filename": entry["filename"],
                "employee_no": employee_no,
                "match_key": employee_no.strip().casefold(),
                "personnel_type": personnel_type.value,
                "extension": entry.get("extension"),
                "file_size": entry.get("file_size", 0),
                "status": entry["status"],
                "detail": entry.get("detail"),
            }
            if entry.get("status") == "pending":
                content_type = _mime_type_from_extension(entry["extension"])
                temp_path = save_bulk_photo_temp(
                    school_uuid=school.uuid,
                    upload_uuid=upload_uuid,
                    content=entry["content"],
                    content_type=content_type,
                )
                uploaded_temp_paths.append(temp_path)
                item.update(content_type=content_type, temp_storage_path=temp_path)
            manifest_entries.append(item)
        manifest_entries = _resolved_entries(
            manifest_entries,
            _personnel_lookup(db, school.id, personnel_type),
        )
    except Exception as exc:
        _safe_delete_paths(uploaded_temp_paths, context="partial personnel temp upload")
        if isinstance(exc, (StorageError, ValueError)):
            raise HTTPException(status_code=503, detail="Temporary photo storage is currently unavailable.") from exc
        raise

    bulk_import = BulkPhotoImport(
        uuid=upload_uuid,
        school_id=school.id,
        user_id=current_user.id,
        manifest=manifest_entries,
        status="uploaded",
        total_files=len(manifest_entries),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=MANIFEST_TTL_HOURS),
    )
    try:
        db.add(bulk_import)
        db.commit()
        db.refresh(bulk_import)
    except Exception:
        db.rollback()
        _safe_delete_paths(uploaded_temp_paths, context="unpersisted personnel temp upload")
        raise
    return PersonnelBulkPhotoUploadResponse(
        manifest_uuid=bulk_import.uuid,
        filename=archive.filename or f"{personnel_type.value}_photos.zip",
        personnel_type=personnel_type,
        total_files=bulk_import.total_files,
        expires_at=bulk_import.expires_at,
    )


@router.post("/{manifest_uuid}/preview", response_model=PersonnelBulkPhotoPreviewResponse)
def preview_bulk_personnel_photos(
    school_uuid: UUID,
    manifest_uuid: UUID,
    personnel_type: PersonnelType,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _authorize(db, current_user, school_uuid)
    manifest = _load_manifest(
        db,
        manifest_uuid=manifest_uuid,
        school_id=school.id,
        user_id=current_user.id,
        personnel_type=personnel_type,
    )
    _ensure_usable(db, manifest, school_uuid=school.uuid)
    return _preview(db, manifest, school.id, personnel_type)


@router.post("/{manifest_uuid}/commit", response_model=PersonnelBulkPhotoCommitResponse)
def commit_bulk_personnel_photos(
    school_uuid: UUID,
    manifest_uuid: UUID,
    personnel_type: PersonnelType,
    payload: PersonnelBulkPhotoCommitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required.")
    school = _authorize(db, current_user, school_uuid)
    manifest = _load_manifest(
        db,
        manifest_uuid=manifest_uuid,
        school_id=school.id,
        user_id=current_user.id,
        personnel_type=personnel_type,
    )
    _ensure_usable(db, manifest, school_uuid=school.uuid)
    personnel = _personnel_lookup(db, school.id, personnel_type)
    results: list[PersonnelBulkPhotoCommitItem] = []
    uploaded = failed = unmatched = invalid = replacements = 0
    all_processed = True
    entries = [dict(item) for item in manifest.manifest]

    for item in entries:
        filename = item.get("filename", "")
        employee_no = item.get("employee_no", "")
        if item.get("status") == "invalid":
            invalid += 1
            all_processed = False
            results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, status="invalid", detail=item.get("detail")))
            continue
        if item.get("status") == "uploaded":
            uploaded += 1
            replacements += int(bool(item.get("replacement")))
            results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, personnel_uuid=item.get("personnel_uuid"), personnel_name=item.get("personnel_name"), status="uploaded", detail="Photo was already uploaded."))
            continue
        record = personnel.get(employee_no.strip().casefold())
        if record is None:
            unmatched += 1
            all_processed = False
            results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, status="unmatched", detail="Personnel was not found in the selected school and type."))
            continue
        temp_path = managed_bulk_photo_temp_storage_path(
            item.get("temp_storage_path"),
            school_uuid=school.uuid,
            upload_uuid=manifest.uuid,
        )
        if temp_path is None:
            failed += 1
            all_processed = False
            results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, personnel_uuid=record.uuid, personnel_name=record.full_name, status="failed", detail="Temporary photo object is missing or invalid."))
            continue

        previous_path = record.photo_path
        had_existing = bool(previous_path)
        public_url: str | None = None
        previous_item = dict(item)
        try:
            photo = download_storage_object(temp_path)
            public_url = save_personnel_photo(
                school.uuid, record.uuid, photo, item.get("content_type")
            )
            record.photo_path = public_url
            record_personnel_audit(
                db,
                personnel=record,
                actor=current_user,
                event_type="personnel_photo_replaced" if had_existing else "personnel_photo_added",
                field_name="photo_path",
                old_value=previous_path,
                new_value=public_url,
                note="Bulk personnel photo import",
            )
            item.update(
                status="uploaded",
                detail="Photo uploaded successfully.",
                personnel_uuid=str(record.uuid),
                personnel_name=record.full_name,
                has_existing_photo=had_existing,
                replacement=had_existing,
            )
            manifest.manifest = entries
            db.commit()
        except Exception:
            db.rollback()
            logger.error("Bulk personnel photo item failed for import %s", manifest.uuid, exc_info=True)
            record.photo_path = previous_path
            item.clear()
            item.update(previous_item)
            manifest.manifest = entries
            new_path = managed_personnel_photo_storage_path(
                public_url, record.uuid, school.uuid
            )
            if new_path is not None:
                _safe_delete_paths([new_path], context="orphaned personnel photo")
            failed += 1
            all_processed = False
            results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, personnel_uuid=record.uuid, personnel_name=record.full_name, status="failed", detail="Photo could not be uploaded."))
            continue

        uploaded += 1
        replacements += int(had_existing)
        results.append(PersonnelBulkPhotoCommitItem(filename=filename, employee_no=employee_no, personnel_uuid=record.uuid, personnel_name=record.full_name, status="uploaded", detail="Photo uploaded successfully."))
        previous_storage_path = managed_personnel_photo_storage_path(
            previous_path, record.uuid, school.uuid
        )
        if previous_storage_path is not None:
            _safe_delete_paths([previous_storage_path], context="replaced personnel photo")
        if _safe_delete_paths([temp_path], context="consumed personnel temp"):
            item["temp_storage_path"] = None

    manifest.manifest = entries
    manifest.status = "completed" if all_processed else "partial"
    db.commit()
    return PersonnelBulkPhotoCommitResponse(
        manifest_uuid=manifest.uuid,
        personnel_type=personnel_type,
        total_files=manifest.total_files,
        uploaded_count=uploaded,
        failed_count=failed,
        unmatched_count=unmatched,
        invalid_count=invalid,
        replacement_count=replacements,
        completed=all_processed,
        items=results,
    )
