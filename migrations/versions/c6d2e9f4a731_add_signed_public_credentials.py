"""Add signed, time-bounded public student credentials.

Revision ID: c6d2e9f4a731
Revises: b4f8c2a91d73
Create Date: 2026-09-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d2e9f4a731"
down_revision: Union[str, Sequence[str], None] = "b4f8c2a91d73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column(
            "public_verification_validity_days",
            sa.Integer(),
            server_default=sa.text("365"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_school_public_verification_validity_days",
        "schools",
        "public_verification_validity_days BETWEEN 1 AND 3650",
    )
    op.add_column(
        "students",
        sa.Column(
            "public_credential_issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "students",
        sa.Column(
            "public_credential_expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now() + interval '365 days'"),
            nullable=False,
        ),
    )
    op.add_column(
        "students",
        sa.Column(
            "public_credential_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_student_public_credential_version",
        "students",
        "public_credential_version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_student_public_credential_version", "students", type_="check"
    )
    op.drop_column("students", "public_credential_version")
    op.drop_column("students", "public_credential_expires_at")
    op.drop_column("students", "public_credential_issued_at")
    op.drop_constraint(
        "ck_school_public_verification_validity_days", "schools", type_="check"
    )
    op.drop_column("schools", "public_verification_validity_days")
