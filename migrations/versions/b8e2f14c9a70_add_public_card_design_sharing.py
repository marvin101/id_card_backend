"""Add revocable public card-design sharing.

Revision ID: b8e2f14c9a70
Revises: a7c91e42d6b3
Create Date: 2026-09-08 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e2f14c9a70"
down_revision: Union[str, Sequence[str], None] = "a7c91e42d6b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "card_templates",
        sa.Column(
            "public_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "card_templates",
        sa.Column("public_token", sa.String(length=96), nullable=True),
    )
    op.create_index(
        "ix_card_templates_public_token",
        "card_templates",
        ["public_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_card_templates_public_token", table_name="card_templates")
    op.drop_column("card_templates", "public_token")
    op.drop_column("card_templates", "public_enabled")
