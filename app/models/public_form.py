from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.school import School
    from app.models.student import Student
    from app.models.users import User


class PublicForm(Base):
    __tablename__ = "public_forms"
    __table_args__ = (
        UniqueConstraint("school_id", name="uq_public_form_school"),
        UniqueConstraint("public_token", name="uq_public_form_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=False, server_default=func.gen_random_uuid()
    )
    school_id: Mapped[int] = mapped_column(
        ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_token: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    require_all_fields: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    allow_photo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_system_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    selected_custom_field_uuids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    success_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    school: Mapped["School"] = relationship(back_populates="public_form")
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped["User | None"] = relationship(foreign_keys=[updated_by_user_id])
    submissions: Mapped[list["PublicFormSubmission"]] = relationship(
        back_populates="form", cascade="all, delete-orphan"
    )


class PublicFormSubmission(Base):
    __tablename__ = "public_form_submissions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_public_form_submission_status"),
        Index("ix_public_form_submissions_school_status_created", "school_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False, server_default=func.gen_random_uuid())
    form_id: Mapped[int] = mapped_column(ForeignKey("public_forms.id", ondelete="CASCADE"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    form: Mapped["PublicForm"] = relationship(back_populates="submissions")
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by_user_id])
    student: Mapped["Student | None"] = relationship(foreign_keys=[student_id])
