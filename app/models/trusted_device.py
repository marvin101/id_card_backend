from datetime import datetime
from uuid import UUID
from sqlalchemy import DateTime, ForeignKey, String, Boolean, Uuid, Index, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class TrustedDevice(Base):
    __tablename__ = "trusted_devices"
    __table_args__ = (Index("ix_trusted_devices_user_active", "user_id", "revoked_at", "expires_at"),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True).with_variant(PGUUID(as_uuid=True), "postgresql"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
