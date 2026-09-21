"""Add school-scoped built-in student field configuration.

Revision ID: 4c8a1f2e6b90
Revises: a2b1c3d4e5f6
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "4c8a1f2e6b90"
down_revision: Union[str, Sequence[str], None] = "a2b1c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_student_field_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("field_key", sa.String(length=50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("display_order >= 0", name="ck_school_student_field_config_order"),
        sa.CheckConstraint("is_enabled OR NOT is_required", name="ck_school_student_field_config_required_enabled"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", "field_key", name="uq_school_student_field_config_key"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_school_student_field_configs_school_id", "school_student_field_configs", ["school_id"])
    connection = op.get_bind()
    can_backend_bypass_rls = connection.exec_driver_sql(
        """
        SELECT r.rolsuper OR r.rolbypassrls OR pg_has_role(current_user, c.relowner, 'MEMBER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
        WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind IN ('r', 'p')
        """,
        ("school_student_field_configs",),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError(
            "Refusing to enable RLS: the backend role cannot bypass RLS for school_student_field_configs"
        )
    op.execute('ALTER TABLE public."school_student_field_configs" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("school_student_field_configs")
