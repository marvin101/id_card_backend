"""Add school-specific user access requests.

Revision ID: 8f31d6a09c42
Revises: 2bd3a7659762
Create Date: 2026-08-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8f31d6a09c42"
down_revision: Union[str, Sequence[str], None] = "2bd3a7659762"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_access_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "school_id",
            name="uq_school_access_requests_user_school",
        ),
    )
    op.create_index(
        "ix_school_access_requests_school_id",
        "school_access_requests",
        ["school_id"],
    )
    op.create_index(
        "ix_school_access_requests_status",
        "school_access_requests",
        ["status"],
    )
    op.create_index(
        "ix_school_access_requests_user_id",
        "school_access_requests",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_school_access_requests_user_id",
        table_name="school_access_requests",
    )
    op.drop_index(
        "ix_school_access_requests_status",
        table_name="school_access_requests",
    )
    op.drop_index(
        "ix_school_access_requests_school_id",
        table_name="school_access_requests",
    )
    op.drop_table("school_access_requests")
