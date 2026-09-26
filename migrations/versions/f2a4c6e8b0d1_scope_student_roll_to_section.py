"""Scope student roll-number uniqueness to a section.

Revision ID: f2a4c6e8b0d1
Revises: c9e2f6a1b4d8
Create Date: 2026-09-26 00:00:00.000000
"""

from alembic import op


revision = "f2a4c6e8b0d1"
down_revision = "c9e2f6a1b4d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_student_roll_school_session_class",
        "students",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_student_roll_school_session_class_section",
        "students",
        ["school_id", "session_id", "class_id", "section_id", "roll_no"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_student_roll_school_session_class_section",
        "students",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_student_roll_school_session_class",
        "students",
        ["school_id", "session_id", "class_id", "roll_no"],
    )
