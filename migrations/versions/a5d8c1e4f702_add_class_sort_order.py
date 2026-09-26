"""add persistent class ordering

Revision ID: a5d8c1e4f702
Revises: f2a4c6e8b0d1
"""

from alembic import op
import sqlalchemy as sa


revision = "a5d8c1e4f702"
down_revision = "f2a4c6e8b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("classes", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ordered AS (
          SELECT id, row_number() OVER (
            PARTITION BY school_id ORDER BY name, id
          ) - 1 AS position
          FROM classes
        )
        UPDATE classes SET sort_order = ordered.position
        FROM ordered WHERE classes.id = ordered.id
        """
    )
    op.alter_column("classes", "sort_order", nullable=False, server_default="0")
    op.create_index(
        "ix_classes_school_sort_order",
        "classes",
        ["school_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_classes_school_sort_order", table_name="classes")
    op.drop_column("classes", "sort_order")
