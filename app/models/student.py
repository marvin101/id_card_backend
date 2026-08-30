from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.academic_session import AcademicSession
    from app.models.school import School
    from app.models.custom_field import StudentCustomFieldValue
    from app.models.school_class import SchoolClass
    from app.models.section import Section


def _academic_session_model():
    from app.models.academic_session import AcademicSession
    return AcademicSession


def _school_class_model():
    from app.models.school_class import SchoolClass
    return SchoolClass


def _section_model():
    from app.models.section import Section
    return Section


class Student(Base):
    __tablename__ = "students"

    # ==========================================================
    # Table Constraints
    # ==========================================================

    __table_args__ = (
        # Admission number is permanently unique within a school.
        UniqueConstraint(
            "school_id",
            "admission_no",
            name="uq_student_admission_school",
        ),

        # Roll number is unique within a school,
        # academic session and class.
        UniqueConstraint(
            "school_id",
            "session_id",
            "class_id",
            "roll_no",
            name="uq_student_roll_school_session_class",
        ),

        ForeignKeyConstraint(
            ["school_id", "session_id"],
            [
                "academic_sessions.school_id",
                "academic_sessions.id",
            ],
            name="fk_student_school_session",
            ondelete="RESTRICT",
        ),

        ForeignKeyConstraint(
            ["school_id", "class_id"],
            [
                "classes.school_id",
                "classes.id",
            ],
            name="fk_student_school_class",
            ondelete="RESTRICT",
        ),

        ForeignKeyConstraint(
            ["class_id", "section_id"],
            [
                "sections.class_id",
                "sections.id",
            ],
            name="fk_student_class_section",
            ondelete="RESTRICT",
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
    # Academic Information
    # ==========================================================

    session_id: Mapped[int] = mapped_column(
        nullable=False,
        index=True,
    )

    class_id: Mapped[int] = mapped_column(
        nullable=False,
        index=True,
    )

    section_id: Mapped[int] = mapped_column(
        nullable=False,
        index=True,
    )

    admission_no: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    roll_no: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    stream: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # ==========================================================
    # Personal Information
    # ==========================================================

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    father_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    mother_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    dob: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    gender: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    blood_group: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,
    )

    # ==========================================================
    # Contact Information
    # ==========================================================

    mobile: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    aadhaar: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ==========================================================
    # Photo
    # ==========================================================

    photo_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

        # ==========================================================
    # Relationships
    # ==========================================================

    academic_session: Mapped["AcademicSession"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            Student.session_id == foreign(_academic_session_model().id),
            Student.school_id == _academic_session_model().school_id,
        ),
        foreign_keys=lambda: [Student.session_id],
    )

    school_class: Mapped["SchoolClass"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            Student.class_id == foreign(_school_class_model().id),
            Student.school_id == _school_class_model().school_id,
        ),
        foreign_keys=lambda: [Student.class_id],
    )

    section: Mapped["Section"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            Student.section_id == foreign(_section_model().id),
            Student.class_id == _section_model().class_id,
        ),
        foreign_keys=lambda: [Student.section_id],
    )

    @property
    def session_uuid(self) -> UUID:
        return self.academic_session.uuid

    @property
    def class_uuid(self) -> UUID:
        return self.school_class.uuid

    @property
    def section_uuid(self) -> UUID:
        return self.section.uuid

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

    school: Mapped["School"] = relationship(
        back_populates="students",
    )

    custom_field_values: Mapped[list["StudentCustomFieldValue"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
        order_by="StudentCustomFieldValue.id",
    )

    @property
    def custom_fields(self) -> list[dict]:
        return [
            {
                "field_uuid": item.field_definition.uuid,
                "field_key": item.field_definition.field_key,
                "label": item.field_definition.label,
                "data_type": item.field_definition.data_type,
                "value": item.value,
                "is_active": item.field_definition.is_active,
            }
            for item in self.custom_field_values
        ]

    academic_session: Mapped["AcademicSession"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            _academic_session_model().id == foreign(Student.session_id),
            _academic_session_model().school_id == Student.school_id,
        ),
        foreign_keys=lambda: [Student.session_id],
    )

    school_class: Mapped["SchoolClass"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            _school_class_model().id == foreign(Student.class_id),
            _school_class_model().school_id == Student.school_id,
        ),
        foreign_keys=lambda: [Student.class_id],
    )

    section: Mapped["Section"] = relationship(
        back_populates="students",
        primaryjoin=lambda: and_(
            _section_model().id == foreign(Student.section_id),
            _section_model().class_id == Student.class_id,
        ),
        foreign_keys=lambda: [Student.section_id],
    )
