from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image
from supabase import create_client

from app.core.config import settings


SUPABASE_BUCKET = "student-photos"

MAX_STUDENT_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_SCHOOL_LOGO_SIZE = 2 * 1024 * 1024  # 2 MB

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

ALLOWED_IMAGE_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

ALLOWED_LOGO_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}


supabase = create_client(
    settings.supabase_url,
    settings.supabase_secret_key,
)


class StorageError(RuntimeError):
    """Raised when persistent object storage cannot complete an operation."""


def validate_student_photo(
    content: bytes,
    content_type: str | None,
) -> str:

    if len(content) > MAX_STUDENT_PHOTO_SIZE:
        raise ValueError("Student photo must not exceed 5 MB.")

    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(
            "Only JPEG, PNG and WebP student photos are allowed."
        )

    try:
        import io

        with Image.open(io.BytesIO(content)) as image:
            image.verify()

    except Exception as exc:
        raise ValueError(
            "The uploaded file is not a valid image."
        ) from exc

    return ALLOWED_IMAGE_TYPES[content_type]


def validate_school_logo(
    content: bytes,
    content_type: str | None,
    filename: str | None,
) -> str:
    if not content:
        raise ValueError("Uploaded logo is empty.")

    if len(content) > MAX_SCHOOL_LOGO_SIZE:
        raise ValueError("School logo must not exceed 2 MB.")

    if content_type not in ALLOWED_LOGO_EXTENSIONS:
        raise ValueError("Only JPEG, PNG and WebP school logos are allowed.")

    extension = Path(filename or "").suffix.lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS[content_type]:
        raise ValueError("School logo filename extension does not match its MIME type.")

    try:
        import io

        with Image.open(io.BytesIO(content)) as image:
            image.verify()
            if image.format != ALLOWED_IMAGE_FORMATS[content_type]:
                raise ValueError(
                    "School logo image content does not match its MIME type."
                )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The uploaded school logo is not a valid image.") from exc

    return ALLOWED_IMAGE_TYPES[content_type]


def save_student_photo(
    student_uuid: UUID,
    content: bytes,
    content_type: str | None,
) -> str:

    extension = validate_student_photo(
        content,
        content_type,
    )

    filename = f"photo{extension}"

    storage_path = (
        f"students/{student_uuid}/{filename}"
    )

    try:
        supabase.storage \
            .from_(SUPABASE_BUCKET) \
            .upload(
                path=storage_path,
                file=content,
                file_options={
                    "content-type": content_type,
                    "upsert": "true",
                },
            )

    except Exception as exc:
        raise ValueError(
            f"Failed to upload student photo: {exc}"
        ) from exc

    public_url = (
        supabase.storage
        .from_(SUPABASE_BUCKET)
        .get_public_url(storage_path)
    )

    return public_url


def save_school_logo(
    school_uuid: UUID,
    content: bytes,
    content_type: str | None,
    filename: str | None,
) -> str:
    extension = validate_school_logo(content, content_type, filename)
    storage_path = f"schools/{school_uuid}/logos/{uuid4().hex}{extension}"

    try:
        supabase.storage.from_(SUPABASE_BUCKET).upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": content_type,
                "upsert": "false",
            },
        )
    except Exception as exc:
        raise StorageError(f"Failed to upload school logo: {exc}") from exc

    return storage_path


def get_storage_public_url(storage_path: str | None) -> str | None:
    if not storage_path:
        return None
    return str(
        supabase.storage.from_(SUPABASE_BUCKET).get_public_url(storage_path)
    )


def delete_storage_object(storage_path: str) -> None:
    try:
        supabase.storage.from_(SUPABASE_BUCKET).remove([storage_path])
    except Exception as exc:
        raise StorageError(f"Failed to delete stored media: {exc}") from exc


def delete_student_photo(
    student_uuid: UUID,
) -> None:
    base_path = f"students/{student_uuid}"

    try:
        files = supabase.storage \
            .from_(SUPABASE_BUCKET) \
            .list(base_path)

        if not files:
            return

        paths = [
            f"{base_path}/{item['name']}"
            for item in files
            if item.get("name")
        ]

        if paths:
            supabase.storage \
                .from_(SUPABASE_BUCKET) \
                .remove(paths)

    except Exception as exc:
        raise ValueError(
            f"Failed to delete student photo: {exc}"
        ) from exc

    # We don't know the extension until we inspect the
    # student's stored path, so deletion is handled by
    # the specific stored object path when needed.
    return
