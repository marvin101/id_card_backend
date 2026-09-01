"""Add school-scoped public student forms.

Revision ID: a7c91e42d6b3
Revises: f3a6c2d9e814
Create Date: 2026-09-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7c91e42d6b3"
down_revision: Union[str, Sequence[str], None] = "f3a6c2d9e814"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_forms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column("public_token", sa.String(96), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("require_all_fields", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allow_photo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_system_fields", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("selected_custom_field_uuids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("success_message", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("school_id", name="uq_public_form_school"),
        sa.UniqueConstraint("public_token", name="uq_public_form_token"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_public_forms_school_id", "public_forms", ["school_id"])
    op.create_index("ix_public_forms_public_token", "public_forms", ["public_token"])

    connection = op.get_bind()
    can_backend_bypass_rls = connection.exec_driver_sql(
        """
        SELECT r.rolsuper OR r.rolbypassrls OR pg_has_role(current_user, c.relowner, 'MEMBER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
        WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind IN ('r', 'p')
        """,
        ("public_forms",),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError("Refusing to enable RLS: the backend role cannot bypass RLS for public_forms")
    op.execute('ALTER TABLE public."public_forms" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("public_forms")
