"""Add auto_admission_format and stream_options to schools

Revision ID: b7e1c2d3f4a5
Revises: a5d8c1e4f702
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7e1c2d3f4a5"
down_revision = "a5d8c1e4f702"
branch_labels = None
depends_on = None


DEFAULT_STREAM_OPTIONS_JSON = (
    '[{"name": "Science", "code": "SCI"}, {"name": "Arts", "code": "ARTS"}, {"name": "Commerce", "code": "COM"}]'
)


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column(
            "auto_admission_format",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "schools",
        sa.Column(
            "stream_options",
            sa.JSON(),
            server_default=sa.text(f"'{DEFAULT_STREAM_OPTIONS_JSON}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("schools", "stream_options")
    op.drop_column("schools", "auto_admission_format")
