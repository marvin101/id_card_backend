from uuid import UUID

from PIL import Image
from supabase import create_client

from app.core.config import settings


SUPABASE_BUCKET = "student-photos"

MAX_STUDENT_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


supabase = create_client(
    settings.supabase_url,
    settings.supabase_secret_key,
)


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