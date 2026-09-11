from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.custom_field import PersonnelCustomFieldValue
    from app.models.personnel_audit_event import PersonnelAuditEvent
    from app.models.school import School
    from app.models.users import User


class Personnel(Base):
    """A teacher or non-teaching staff identity owned by one school.

    Personnel identities are intentionally independent from authentication
    accounts. ``linked_user_id`` is reserved for an explicit association and
    must never be inferred from a user's school role.
    """

    __tablename__ = "personnel"
    __table_args__ = (
        CheckConstraint(
            "personnel_type IN ('teacher', 'staff')",
            name="ck_personnel_type",
        ),
        CheckConstraint(
            "verification_status IN ('pending', 'needs_correction', 'verified')",
            name="ck_personnel_verification_status",
        ),
        CheckConstraint("print_count >= 0", name="ck_personnel_print_count"),
        UniqueConstraint(
            "school_id", "employee_no", name="uq_personnel_employee_school"
        ),
        UniqueConstraint(
            "school_id", "linked_user_id", name="uq_personnel_linked_user_school"
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
    linked_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    personnel_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    employee_no: Mapped[str] = mapped_column(String(50), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(5), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    verification_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    correction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    printed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    print_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    school: Mapped["School"] = relationship(back_populates="personnel")
    linked_user: Mapped["User | None"] = relationship(
        foreign_keys=[linked_user_id]
    )
    verified_by: Mapped["User | None"] = relationship(
        foreign_keys=[verified_by_user_id]
    )
    printed_by: Mapped["User | None"] = relationship(
        foreign_keys=[printed_by_user_id]
    )
    custom_field_values: Mapped[list["PersonnelCustomFieldValue"]] = relationship(
        back_populates="personnel",
        cascade="all, delete-orphan",
        order_by="PersonnelCustomFieldValue.id",
    )
    audit_events: Mapped[list["PersonnelAuditEvent"]] = relationship(
        back_populates="personnel", cascade="all, delete-orphan"
    )

    @property
    def linked_user_uuid(self) -> UUID | None:
        return self.linked_user.uuid if self.linked_user is not None else None

    @property
    def verified_by_user_uuid(self) -> UUID | None:
        return self.verified_by.uuid if self.verified_by is not None else None

    @property
    def verified_by_name(self) -> str | None:
        return self.verified_by.full_name if self.verified_by is not None else None

    @property
    def printed_by_user_uuid(self) -> UUID | None:
        return self.printed_by.uuid if self.printed_by is not None else None

    @property
    def printed_by_name(self) -> str | None:
        return self.printed_by.full_name if self.printed_by is not None else None

    @property
    def lifecycle_status(self) -> str:
        if self.verification_status != "verified":
            return self.verification_status
        return "printed" if self.print_count > 0 else "ready_for_print"

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
