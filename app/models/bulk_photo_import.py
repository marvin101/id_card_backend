from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BulkPhotoImport(Base):
    __tablename__ = "bulk_photo_imports"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        unique=True,
        nullable=False,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )

    school_id: Mapped[int] = mapped_column(
        ForeignKey(
            "schools.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Metadata-only manifest. Temporary image binaries are stored in
    # Supabase Storage and referenced by a guarded temp_storage_path.
    # Raw bytes, base64 payloads, signed URLs, and credentials must never
    # be persisted here.
    manifest: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="uploaded",
        server_default="uploaded",
    )

    total_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
