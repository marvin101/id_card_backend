from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

from PIL import Image


MAX_ZIP_SIZE = 25 * 1024 * 1024
MAX_FILES = 5000
MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
MAX_IMAGE_SIZE = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

FORMAT_BY_EXTENSION = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


class BulkPhotoValidationError(ValueError):
    pass


def validate_archive_size(content: bytes) -> None:
    if not content:
        raise BulkPhotoValidationError(
            "Uploaded ZIP archive is empty."
        )

    if len(content) > MAX_ZIP_SIZE:
        raise BulkPhotoValidationError(
            "ZIP archive must not exceed 25 MB."
        )


def safe_archive_filename(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)

    if path.is_absolute():
        raise BulkPhotoValidationError(
            f"Unsafe archive path: {filename}"
        )

    if ".." in path.parts:
        raise BulkPhotoValidationError(
            f"Unsafe archive path: {filename}"
        )

    if len(path.parts) != 1:
        raise BulkPhotoValidationError(
            f"Nested archive paths are not allowed: {filename}"
        )

    return path.name


def admission_no_from_filename(filename: str) -> str:
    return PurePosixPath(filename).stem.strip()


def _validate_image(
    content: bytes,
    extension: str,
) -> None:

    if not content:
        raise ValueError("Image is empty.")

    if len(content) > MAX_IMAGE_SIZE:
        raise ValueError(
            "Image exceeds the 5 MB limit."
        )

    expected_format = FORMAT_BY_EXTENSION[
        extension
    ]

    try:
        with Image.open(
            io.BytesIO(content)
        ) as image:

            image.verify()

        with Image.open(
            io.BytesIO(content)
        ) as image:

            if image.format != expected_format:
                raise ValueError(
                    "Image content does not match "
                    "its filename extension."
                )

    except ValueError:
        raise

    except Exception as exc:
        raise ValueError(
            "The file is not a valid image."
        ) from exc


def inspect_zip(
    content: bytes,
) -> list[dict]:

    validate_archive_size(content)

    try:
        archive = zipfile.ZipFile(
            io.BytesIO(content)
        )
    except zipfile.BadZipFile as exc:
        raise BulkPhotoValidationError(
            "The uploaded file is not a valid ZIP archive."
        ) from exc

    all_infos = archive.infolist()

    if any(info.is_dir() for info in all_infos):
        raise BulkPhotoValidationError(
            "Directory entries are not allowed in the ZIP archive."
        )

    infos = all_infos

    if not infos:
        raise BulkPhotoValidationError(
            "ZIP archive contains no files."
        )

    if len(infos) > MAX_FILES:
        raise BulkPhotoValidationError(
            f"ZIP archive contains more than {MAX_FILES} files."
        )

    total_uncompressed = sum(
        info.file_size
        for info in infos
    )

    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
        raise BulkPhotoValidationError(
            "Expanded ZIP contents must not exceed 100 MB."
        )

    try:
        if archive.testzip() is not None:
            raise BulkPhotoValidationError(
                "ZIP archive is corrupt."
            )
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise BulkPhotoValidationError(
            "ZIP archive is corrupt or unreadable."
        ) from exc

    result: list[dict] = []

    seen_admission_numbers: set[str] = set()

    for info in infos:

        filename = safe_archive_filename(
            info.filename
        )

        extension = (
            PurePosixPath(filename)
            .suffix
            .lower()
        )

        admission_no = admission_no_from_filename(
            filename
        )

        if extension not in ALLOWED_EXTENSIONS:

            result.append(
                {
                    "filename": filename,
                    "admission_no": admission_no,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": (
                        "Only JPG, JPEG, PNG and "
                        "WebP files are supported."
                    ),
                }
            )

            continue

        if not admission_no:

            result.append(
                {
                    "filename": filename,
                    "admission_no": "",
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": (
                        "Filename must contain "
                        "an admission number."
                    ),
                }
            )

            continue

        admission_key = admission_no.casefold()

        if admission_key in seen_admission_numbers:

            result.append(
                {
                    "filename": filename,
                    "admission_no": admission_no,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": (
                        "Duplicate admission number "
                        "in archive."
                    ),
                }
            )

            continue

        seen_admission_numbers.add(
            admission_key
        )

        if info.file_size > MAX_IMAGE_SIZE:

            result.append(
                {
                    "filename": filename,
                    "admission_no": admission_no,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": (
                        "Image exceeds the 5 MB limit."
                    ),
                }
            )

            continue

        try:
            image_content = archive.read(info)

            _validate_image(
                image_content,
                extension,
            )

        except (ValueError, RuntimeError, zipfile.BadZipFile) as exc:

            result.append(
                {
                    "filename": filename,
                    "admission_no": admission_no,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": str(exc),
                }
            )

            continue

        result.append(
            {
                "filename": filename,
                "admission_no": admission_no,
                "status": "pending",
                "content": image_content,
                "extension": extension,
                "file_size": len(image_content),
            }
        )

    return result
