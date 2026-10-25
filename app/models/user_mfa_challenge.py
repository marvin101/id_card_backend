"""Persistent, single-use MFA login challenges."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserMfaChallenge(Base):
    __tablename__ = "user_mfa_challenges"
    __table_args__ = (
        Index("ix_user_mfa_challenges_user_id", "user_id"),
        Index("ix_user_mfa_challenges_expires_at", "expires_at"),
    )

    # JWT jti, also the primary key for atomic one-time consumption.
    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
