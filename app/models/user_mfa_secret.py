"""TOTP multi-factor authentication secrets per user."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserMfaSecret(Base):
    __tablename__ = "user_mfa_secrets"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            name="user_mfa_secrets_user_id_key",
        ),
        Index(
            "ix_user_mfa_secrets_user_id",
            "user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The shared secret is encrypted at rest so a database compromise does not
    # expose every TOTP seed in the clear.
    encrypted_secret: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    # A deterministic, non-secret identifier used to build provisioning URIs
    # without decrypting the shared secret. It is not a credential on its own.
    issuer_label: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    
    # Last TOTP time-step accepted for login/enrollment; prevents code replay.
    last_totp_counter: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
