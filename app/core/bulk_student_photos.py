from __future__ import annotations

import io
import stat
import zipfile
from collections import Counter
from pathlib import PurePosixPath

from PIL import Image


MAX_ZIP_SIZE = 25 * 1024 * 1024
MAX_FILES = 5000
MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

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

    if path.is_absolute() or (path.parts and path.parts[0].endswith(":")):
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


def _is_ignored_system_entry(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(path.parts and path.parts[0] == "__MACOSX")
        or path.name == ".DS_Store"
        or path.name.startswith("._")
    )


def _is_symbolic_link(info: zipfile.ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    return stat.S_IFMT(unix_mode) == stat.S_IFLNK


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
    *,
    identifier_key: str = "admission_no",
    identifier_label: str = "admission number",
    mark_all_duplicates: bool = False,
    duplicate_status: str = "invalid",
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

    for info in all_infos:
        normalized = info.filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or ".." in path.parts
            or (path.parts and path.parts[0].endswith(":"))
        ):
            raise BulkPhotoValidationError(
                f"Unsafe archive path: {info.filename}"
            )

    if any(_is_symbolic_link(info) for info in all_infos):
        raise BulkPhotoValidationError(
            "Symbolic links are not allowed in the ZIP archive."
        )

    if any(info.flag_bits & 0x1 for info in all_infos):
        raise BulkPhotoValidationError(
            "Encrypted ZIP entries are not supported."
        )

    infos = [
        info
        for info in all_infos
        if not _is_ignored_system_entry(info.filename)
    ]

    if any(info.is_dir() for info in infos):
        raise BulkPhotoValidationError(
            "Directory entries are not allowed in the ZIP archive."
        )

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

    if any(
        info.file_size > 0
        and (
            info.compress_size == 0
            or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        )
        for info in infos
    ):
        raise BulkPhotoValidationError(
            "ZIP entry compression ratio exceeds the 100:1 safety limit."
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
    safe_names = [safe_archive_filename(info.filename) for info in infos]
    filename_counts = Counter(name.casefold() for name in safe_names)
    identifier_counts = Counter(
        admission_no_from_filename(name).casefold()
        for name in safe_names
        if admission_no_from_filename(name)
    )
    seen_identifiers: set[str] = set()

    for info, filename in zip(infos, safe_names, strict=True):

        extension = (
            PurePosixPath(filename)
            .suffix
            .lower()
        )

        identifier = admission_no_from_filename(
            filename
        )

        if extension not in ALLOWED_EXTENSIONS:

            result.append(
                {
                    "filename": filename,
                    identifier_key: identifier,
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

        if not identifier:

            result.append(
                {
                    "filename": filename,
                    identifier_key: "",
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": "invalid",
                    "detail": (
                        "Filename must contain "
                        f"a {identifier_label}."
                    ),
                }
            )

            continue

        identifier_value = identifier.casefold()

        if mark_all_duplicates and filename_counts[filename.casefold()] > 1:

            result.append(
                {
                    "filename": filename,
                    identifier_key: identifier,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": duplicate_status,
                    "detail": "Duplicate filename in archive.",
                }
            )

            continue

        if (
            mark_all_duplicates and identifier_counts[identifier_value] > 1
        ) or identifier_value in seen_identifiers:

            result.append(
                {
                    "filename": filename,
                    identifier_key: identifier,
                    "extension": extension,
                    "file_size": info.file_size,
                    "status": duplicate_status,
                    "detail": (
                        f"Multiple files map to the same {identifier_label}."
                    ),
                }
            )

            continue

        seen_identifiers.add(identifier_value)

        if info.file_size > MAX_IMAGE_SIZE:

            result.append(
                {
                    "filename": filename,
                    identifier_key: identifier,
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
                    identifier_key: identifier,
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
                identifier_key: identifier,
                "status": "pending",
                "content": image_content,
                "extension": extension,
                "file_size": len(image_content),
            }
        )

    return result
