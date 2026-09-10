"""Add optional back-side card design.

Revision ID: b4f8c2a91d73
Revises: a1d4e7f9b2c5
Create Date: 2026-09-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b4f8c2a91d73"
down_revision: Union[str, Sequence[str], None] = "a1d4e7f9b2c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "card_templates",
        sa.Column(
            "back_design",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("card_templates", "back_design")
