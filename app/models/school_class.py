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
    from app.models.school import School
    from app.models.section import Section
    from app.models.student import Student


def _student_model():
    from app.models.student import Student
    return Student

class SchoolClass(Base):
    __tablename__ = "classes"

    # ==========================================================
    # Table Constraints
    # ==========================================================

    __table_args__ = (
        # A school cannot have two classes with the same name.
        UniqueConstraint(
            "school_id",
            "name",
            name="uq_class_school_name",
        ),

        # Allows Student to reference a class together with
        # its school, ensuring the class belongs to that school.
        UniqueConstraint(
            "id",
            "school_id",
            name="uq_class_id_school",
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
    # School
    # ==========================================================

    school_id: Mapped[int] = mapped_column(
        ForeignKey(
            "schools.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # ==========================================================
    # Class Information
    # ==========================================================

    name: Mapped[str] = mapped_column(
        String(50),
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

    school: Mapped["School"] = relationship(
        back_populates="classes",
    )

    sections: Mapped[list["Section"]] = relationship(
        back_populates="school_class",
        cascade="all, delete-orphan",
    )

    students: Mapped[list["Student"]] = relationship(
    back_populates="school_class",
    primaryjoin=lambda: and_(
        SchoolClass.id == foreign(_student_model().class_id),
        SchoolClass.school_id == _student_model().school_id,
    ),
    foreign_keys=lambda: [_student_model().class_id],
)