"""Add user designation and explicit platform role.

Revision ID: c4a9f10e2b71
Revises: 7e38adb611d3
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4a9f10e2b71"
down_revision: Union[str, Sequence[str], None] = "7e38adb611d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add only the two user fields needed by the new authorization model."""
    op.add_column("users", sa.Column("designation", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("platform_role", sa.String(length=30), nullable=True))

    # Preserve effective authority for existing platform administrators while
    # the legacy boolean remains a deployment-safe fallback.
    op.execute(
        "UPDATE users SET platform_role = 'platform_admin' "
        "WHERE is_platform_admin = true"
    )


def downgrade() -> None:
    op.drop_column("users", "platform_role")
    op.drop_column("users", "designation")
