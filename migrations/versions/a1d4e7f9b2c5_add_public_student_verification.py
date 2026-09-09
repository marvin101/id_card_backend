"""Add revocable public student verification links.

Revision ID: a1d4e7f9b2c5
Revises: e4c7a91d2f60
Create Date: 2026-09-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1d4e7f9b2c5"
down_revision: Union[str, Sequence[str], None] = "e4c7a91d2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column(
            "public_verification_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "schools",
        sa.Column(
            "public_verification_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(
                "'[\"full_name\", \"admission_no\", \"class\", \"section\"]'::jsonb"
            ),
            nullable=False,
        ),
    )
    op.add_column(
        "students",
        sa.Column("public_verification_token", sa.String(length=96), nullable=True),
    )
    op.add_column(
        "students",
        sa.Column(
            "public_verification_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE students
        SET public_verification_token =
            replace(gen_random_uuid()::text, '-', '') ||
            replace(gen_random_uuid()::text, '-', '')
        WHERE public_verification_token IS NULL
        """
    )
    op.alter_column("students", "public_verification_token", nullable=False)
    op.create_index(
        "ix_students_public_verification_token",
        "students",
        ["public_verification_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_students_public_verification_token", table_name="students")
    op.drop_column("students", "public_verification_enabled")
    op.drop_column("students", "public_verification_token")
    op.drop_column("schools", "public_verification_fields")
    op.drop_column("schools", "public_verification_enabled")
