from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    and_,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.school_class import SchoolClass
    from app.models.student import Student

def _student_model():
    from app.models.student import Student
    return Student

class Section(Base):
    __tablename__ = "sections"

    # ==========================================================
    # Table Constraints
    # ==========================================================

    __table_args__ = (
        # A class cannot have two sections with the same name.
        UniqueConstraint(
            "class_id",
            "name",
            name="uq_section_class_name",
        ),

        # Allows Student to reference a section together with
        # its class, ensuring the section belongs to that class.
        UniqueConstraint(
            "id",
            "class_id",
            name="uq_section_id_class",
        ),
    )

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
    # Class
    # ==========================================================

    class_id: Mapped[int] = mapped_column(
        ForeignKey(
            "classes.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # ==========================================================
    # Section Information
    # ==========================================================

    name: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
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

    school_class: Mapped["SchoolClass"] = relationship(
        back_populates="sections",
    )

    students: Mapped[list["Student"]] = relationship(
    back_populates="section",
    primaryjoin=lambda: and_(
        Section.id == foreign(_student_model().section_id),
        Section.class_id == _student_model().class_id,
    ),
    foreign_keys=lambda: [_student_model().section_id],
)