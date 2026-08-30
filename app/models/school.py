from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.academic_session import AcademicSession
    from app.models.card_template import CardTemplate
    from app.models.custom_field import CustomFieldDefinition
    from app.models.school_class import SchoolClass
    from app.models.student import Student
    from app.models.user_school_access import UserSchoolAccess


class School(Base):
    __tablename__ = "schools"

    # ==========================================================
    # Primary Key
    # ==========================================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    # ==========================================================
    # Public UUID
    # ==========================================================

    uuid: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        unique=True,
        nullable=False,
        server_default=func.gen_random_uuid(),
    )

    # ==========================================================
    # School Identity
    # ==========================================================

    school_code: Mapped[str] = mapped_column(
        String(30),
        unique=True,
        nullable=False,
    )

    school_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # ==========================================================
    # Contact Information
    # ==========================================================

    email: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    website: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # ==========================================================
    # Address
    # ==========================================================

    address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    city: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    district: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    state: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    country: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default="India",
    )

    postal_code: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # ==========================================================
    # School Details
    # ==========================================================

    logo_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    principal_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    # ==========================================================
    # Status
    # ==========================================================

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    # ==========================================================
    # Timestamps
    # ==========================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ==========================================================
    # Relationships
    # ==========================================================

    user_access: Mapped[list["UserSchoolAccess"]] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
    )

    academic_sessions: Mapped[list["AcademicSession"]] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
    )

    classes: Mapped[list["SchoolClass"]] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
    )

    students: Mapped[list["Student"]] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
    )

    custom_field_definitions: Mapped[list["CustomFieldDefinition"]] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
    )

    card_template: Mapped["CardTemplate | None"] = relationship(
        back_populates="school",
        cascade="all, delete-orphan",
        uselist=False,
    )
