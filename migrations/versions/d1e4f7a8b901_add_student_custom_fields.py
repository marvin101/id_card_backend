"""Add dynamic student custom field definitions and values.

Revision ID: d1e4f7a8b901
Revises: 9b6f3e21a4d7
Create Date: 2026-08-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d1e4f7a8b901"
down_revision: Union[str, Sequence[str], None] = "9b6f3e21a4d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("field_key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("data_type IN ('text', 'multiline', 'number', 'date', 'phone')", name="ck_custom_field_data_type"),
        sa.CheckConstraint("display_order >= 0", name="ck_custom_field_display_order"),
        sa.CheckConstraint("entity_type IN ('student')", name="ck_custom_field_entity_type"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", "entity_type", "field_key", name="uq_custom_field_school_entity_key"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_custom_field_definitions_school_id", "custom_field_definitions", ["school_id"])
    op.create_index("ix_custom_field_school_entity_order", "custom_field_definitions", ["school_id", "entity_type", "display_order"])
    op.create_table(
        "student_custom_field_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("field_definition_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["field_definition_id"], ["custom_field_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "field_definition_id", name="uq_student_custom_field_value"),
    )
    op.create_index("ix_student_custom_field_values_student_id", "student_custom_field_values", ["student_id"])
    op.create_index("ix_student_custom_field_values_field_definition_id", "student_custom_field_values", ["field_definition_id"])
    connection = op.get_bind()
    for table_name in ("custom_field_definitions", "student_custom_field_values"):
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
        op.execute(
            f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'
        )


def downgrade() -> None:
    op.drop_table("student_custom_field_values")
    op.drop_table("custom_field_definitions")
