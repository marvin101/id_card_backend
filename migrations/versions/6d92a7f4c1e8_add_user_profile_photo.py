"""Add user profile photo storage path.

Revision ID: 6d92a7f4c1e8
Revises: 4c8a1f2e6b90
"""
from alembic import op
import sqlalchemy as sa


revision = "6d92a7f4c1e8"
down_revision = "4c8a1f2e6b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("profile_photo_path", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "profile_photo_path")
