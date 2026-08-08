from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    and_,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.school import School
    from app.models.student import Student


def _student_model():
    from app.models.student import Student
    return Student

class AcademicSession(Base):
    __tablename__ = "academic_sessions"

    # ==========================================================
    # Table Constraints
    # ==========================================================

    __table_args__ = (
        # A school cannot have two sessions with the same name.
        UniqueConstraint(
            "school_id",
            "name",
            name="uq_academic_session_school_name",
        ),

        # Allows Student to reference a session together with
        # its school, ensuring the session belongs to that school.
        UniqueConstraint(
            "id",
            "school_id",
            name="uq_academic_session_id_school",
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
    # Session Information
    # ==========================================================

    name: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
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
        back_populates="academic_sessions",
    )

    students: Mapped[list["Student"]] = relationship(
    back_populates="academic_session",
    primaryjoin=lambda: (
        AcademicSession.id
        == foreign(
            __import__(
                "app.models.student",
                fromlist=["Student"],
            ).Student.session_id
        )
    ) & (
        AcademicSession.school_id
        == __import__(
            "app.models.student",
            fromlist=["Student"],
        ).Student.school_id
    ),
    foreign_keys=lambda: [
        __import__(
            "app.models.student",
            fromlist=["Student"],
        ).Student.session_id
    ],
)