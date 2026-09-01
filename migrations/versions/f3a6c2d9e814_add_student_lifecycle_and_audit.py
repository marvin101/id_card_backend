"""Add student verification, printed lifecycle, and audit history.

Revision ID: f3a6c2d9e814
Revises: 5683bf325b3d
Create Date: 2026-09-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a6c2d9e814"
down_revision: Union[str, Sequence[str], None] = "5683bf325b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("students", sa.Column("verification_status", sa.String(30), server_default="pending", nullable=False))
    op.add_column("students", sa.Column("correction_note", sa.Text(), nullable=True))
    op.add_column("students", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("verified_by_user_id", sa.Integer(), nullable=True))
    op.add_column("students", sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("printed_by_user_id", sa.Integer(), nullable=True))
    op.add_column("students", sa.Column("print_count", sa.Integer(), server_default="0", nullable=False))
    op.create_check_constraint("ck_student_verification_status", "students", "verification_status IN ('pending', 'needs_correction', 'verified')")
    op.create_check_constraint("ck_student_print_count", "students", "print_count >= 0")
    op.create_foreign_key("fk_student_verified_by_user", "students", "users", ["verified_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_student_printed_by_user", "students", "users", ["printed_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_students_verification_status", "students", ["verification_status"])
    op.create_index("ix_students_print_count", "students", ["print_count"])
    op.create_index("ix_students_school_verification", "students", ["school_id", "verification_status"])
    op.create_index("ix_students_school_print_count", "students", ["school_id", "print_count"])

    op.create_table(
        "student_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_student_audit_events_school_id", "student_audit_events", ["school_id"])
    op.create_index("ix_student_audit_events_student_id", "student_audit_events", ["student_id"])
    op.create_index("ix_student_audit_events_actor_user_id", "student_audit_events", ["actor_user_id"])
    op.create_index("ix_student_audit_events_created_at", "student_audit_events", ["created_at"])
    op.create_index("ix_student_audit_school_student_created", "student_audit_events", ["school_id", "student_id", "created_at"])
    op.create_index("ix_student_audit_school_event_created", "student_audit_events", ["school_id", "event_type", "created_at"])

    connection = op.get_bind()
    can_backend_bypass_rls = connection.exec_driver_sql(
        """
        SELECT r.rolsuper OR r.rolbypassrls OR pg_has_role(current_user, c.relowner, 'MEMBER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
        WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind IN ('r', 'p')
        """,
        ("student_audit_events",),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError("Refusing to enable RLS: the backend role cannot bypass RLS for student_audit_events")
    op.execute('ALTER TABLE public."student_audit_events" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("student_audit_events")
    op.drop_index("ix_students_school_print_count", table_name="students")
    op.drop_index("ix_students_school_verification", table_name="students")
    op.drop_index("ix_students_print_count", table_name="students")
    op.drop_index("ix_students_verification_status", table_name="students")
    op.drop_constraint("fk_student_printed_by_user", "students", type_="foreignkey")
    op.drop_constraint("fk_student_verified_by_user", "students", type_="foreignkey")
    op.drop_constraint("ck_student_print_count", "students", type_="check")
    op.drop_constraint("ck_student_verification_status", "students", type_="check")
    for column in ("print_count", "printed_by_user_id", "printed_at", "verified_by_user_id", "verified_at", "correction_note", "verification_status"):
        op.drop_column("students", column)
