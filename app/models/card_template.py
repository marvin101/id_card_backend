from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.school import School


class CardTemplate(Base):
    __tablename__ = "card_templates"
    __table_args__ = (UniqueConstraint("school_id", name="uq_card_templates_school_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=False,
        server_default=func.gen_random_uuid(),
    )
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    design: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    back_design: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    public_token: Mapped[str | None] = mapped_column(
        String(96), unique=True, nullable=True, index=True,
    )
    public_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    school: Mapped["School"] = relationship(back_populates="card_template")
