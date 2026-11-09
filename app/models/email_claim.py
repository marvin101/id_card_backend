from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailClaim(Base):
    __tablename__ = "email_claims"

    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('current', 'pending')",
            name="ck_email_claims_claim_type",
        ),
        Index(
            "uq_email_claims_user_current",
            "user_id",
            unique=True,
            postgresql_where=text("claim_type = 'current'"),
            sqlite_where=text("claim_type = 'current'"),
        ),
        Index(
            "uq_email_claims_user_pending",
            "user_id",
            unique=True,
            postgresql_where=text("claim_type = 'pending'"),
            sqlite_where=text("claim_type = 'pending'"),
        ),
    )

    # Globally unique, normalized email address.
    email_key: Mapped[str] = mapped_column(
        String(150),
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    claim_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )