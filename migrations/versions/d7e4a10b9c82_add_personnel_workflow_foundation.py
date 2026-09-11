"""Add teacher and non-teaching staff workflow foundation.

Revision ID: d7e4a10b9c82
Revises: c6d2e9f4a731
Create Date: 2026-09-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7e4a10b9c82"
down_revision: Union[str, Sequence[str], None] = "c6d2e9f4a731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_deny_by_default_rls(table_names: tuple[str, ...]) -> None:
    connection = op.get_bind()
    for table_name in table_names:
        can_backend_bypass_rls = connection.exec_driver_sql(
            """
            SELECT r.rolsuper
                OR r.rolbypassrls
                OR pg_has_role(current_user, c.relowner, 'MEMBER')
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
            WHERE n.nspname = 'public'
              AND c.relname = %s
              AND c.relkind IN ('r', 'p')
            """,
            (table_name,),
        ).scalar_one_or_none()
        if can_backend_bypass_rls is not True:
            raise RuntimeError(
                "Refusing to enable RLS: the configured backend database "
                f"role cannot bypass RLS for public.{table_name}"
            )
        op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.drop_constraint(
        "ck_custom_field_entity_type", "custom_field_definitions", type_="check"
    )
    op.create_check_constraint(
        "ck_custom_field_entity_type",
        "custom_field_definitions",
        "entity_type IN ('student', 'teacher', 'staff')",
    )

    op.create_table(
        "personnel",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("linked_user_id", sa.Integer(), nullable=True),
        sa.Column("personnel_type", sa.String(length=20), nullable=False),
        sa.Column("employee_no", sa.String(length=50), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column("designation", sa.String(length=120), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("dob", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(length=20), nullable=True),
        sa.Column("blood_group", sa.String(length=5), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=150), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=30),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("correction_note", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Integer(), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("printed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("print_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "personnel_type IN ('teacher', 'staff')", name="ck_personnel_type"
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'needs_correction', 'verified')",
            name="ck_personnel_verification_status",
        ),
        sa.CheckConstraint("print_count >= 0", name="ck_personnel_print_count"),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["linked_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["printed_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint(
            "school_id", "employee_no", name="uq_personnel_employee_school"
        ),
        sa.UniqueConstraint(
            "school_id", "linked_user_id", name="uq_personnel_linked_user_school"
        ),
    )
    op.create_index("ix_personnel_school_id", "personnel", ["school_id"])
    op.create_index("ix_personnel_linked_user_id", "personnel", ["linked_user_id"])
    op.create_index("ix_personnel_personnel_type", "personnel", ["personnel_type"])
    op.create_index(
        "ix_personnel_verification_status", "personnel", ["verification_status"]
    )
    op.create_index("ix_personnel_print_count", "personnel", ["print_count"])
    op.create_index(
        "ix_personnel_school_type_active",
        "personnel",
        ["school_id", "personnel_type", "is_active"],
    )
    op.create_index(
        "ix_personnel_school_verification",
        "personnel",
        ["school_id", "verification_status"],
    )
    op.create_index(
        "ix_personnel_school_print_count",
        "personnel",
        ["school_id", "print_count"],
    )

    op.create_table(
        "personnel_custom_field_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("personnel_id", sa.Integer(), nullable=False),
        sa.Column("field_definition_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["field_definition_id"],
            ["custom_field_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "personnel_id",
            "field_definition_id",
            name="uq_personnel_custom_field_value",
        ),
    )
    op.create_index(
        "ix_personnel_custom_field_values_personnel_id",
        "personnel_custom_field_values",
        ["personnel_id"],
    )
    op.create_index(
        "ix_personnel_custom_field_values_field_definition_id",
        "personnel_custom_field_values",
        ["field_definition_id"],
    )

    op.create_table(
        "personnel_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("personnel_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=True),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["schools.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["personnel_id"], ["personnel.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index(
        "ix_personnel_audit_events_school_id", "personnel_audit_events", ["school_id"]
    )
    op.create_index(
        "ix_personnel_audit_events_personnel_id",
        "personnel_audit_events",
        ["personnel_id"],
    )
    op.create_index(
        "ix_personnel_audit_events_actor_user_id",
        "personnel_audit_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_personnel_audit_events_created_at",
        "personnel_audit_events",
        ["created_at"],
    )
    op.create_index(
        "ix_personnel_audit_school_personnel_created",
        "personnel_audit_events",
        ["school_id", "personnel_id", "created_at"],
    )
    op.create_index(
        "ix_personnel_audit_school_event_created",
        "personnel_audit_events",
        ["school_id", "event_type", "created_at"],
    )

    _enable_deny_by_default_rls(
        ("personnel", "personnel_custom_field_values", "personnel_audit_events")
    )


def downgrade() -> None:
    op.drop_table("personnel_audit_events")
    op.drop_table("personnel_custom_field_values")
    op.drop_table("personnel")
    op.execute(
        "DELETE FROM custom_field_definitions "
        "WHERE entity_type IN ('teacher', 'staff')"
    )
    op.drop_constraint(
        "ck_custom_field_entity_type", "custom_field_definitions", type_="check"
    )
    op.create_check_constraint(
        "ck_custom_field_entity_type",
        "custom_field_definitions",
        "entity_type IN ('student')",
    )
