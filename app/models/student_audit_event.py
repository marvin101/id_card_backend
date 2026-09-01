from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.student import Student
    from app.models.users import User


class StudentAuditEvent(Base):
    __tablename__ = "student_audit_events"
    __table_args__ = (
        Index(
            "ix_student_audit_school_student_created",
            "school_id",
            "student_id",
            "created_at",
        ),
        Index(
            "ix_student_audit_school_event_created",
            "school_id",
            "event_type",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        unique=True,
        nullable=False,
        server_default=func.gen_random_uuid(),
    )
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    student: Mapped["Student"] = relationship(back_populates="audit_events")
    actor: Mapped["User | None"] = relationship(foreign_keys=[actor_user_id])

    @property
    def actor_user_uuid(self) -> UUID | None:
        return self.actor.uuid if self.actor is not None else None

    @property
    def actor_name(self) -> str | None:
        return self.actor.full_name if self.actor is not None else None
