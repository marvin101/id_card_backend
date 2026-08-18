from pathlib import Path
from uuid import UUID

from PIL import Image


BASE_DIR = Path(__file__).resolve().parents[2]
STUDENT_UPLOAD_DIR = BASE_DIR / "uploads" / "students"

MAX_STUDENT_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
        with Image.open(__import__("io").BytesIO(content)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("The uploaded file is not a valid image.") from exc

    return ALLOWED_IMAGE_TYPES[content_type]


def save_student_photo(
    student_uuid: UUID,
    content: bytes,
    content_type: str | None,
) -> str:
    extension = validate_student_photo(content, content_type)

    student_dir = STUDENT_UPLOAD_DIR / str(student_uuid)
    student_dir.mkdir(parents=True, exist_ok=True)

    # Remove any previous student photo.
    for existing_file in student_dir.iterdir():
        if existing_file.is_file():
            existing_file.unlink()

    filename = f"photo{extension}"
    file_path = student_dir / filename

    file_path.write_bytes(content)

    return f"/media/students/{student_uuid}/{filename}"


def delete_student_photo(student_uuid: UUID) -> None:
    student_dir = STUDENT_UPLOAD_DIR / str(student_uuid)

    if not student_dir.exists():
        return

    for existing_file in student_dir.iterdir():
        if existing_file.is_file():
            existing_file.unlink()

    try:
        student_dir.rmdir()
    except OSError:
        pass