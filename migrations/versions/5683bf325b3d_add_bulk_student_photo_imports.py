"""add bulk student photo imports

Revision ID: 5683bf325b3d
Revises: d1e4f7a8b901
Create Date: 2026-08-30 21:42:16.076080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql



# revision identifiers, used by Alembic.
revision: str = '5683bf325b3d'
down_revision: Union[str, Sequence[str], None] = 'd1e4f7a8b901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bulk_photo_imports",

        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),

        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),

        sa.Column(
            "school_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "manifest",
            postgresql.JSONB(),
            nullable=False,
        ),

        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="uploaded",
        ),

        sa.Column(
            "total_files",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),

        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )

    op.create_index(
        "ix_bulk_photo_imports_school_id",
        "bulk_photo_imports",
        ["school_id"],
    )

    op.create_index(
        "ix_bulk_photo_imports_user_id",
        "bulk_photo_imports",
        ["user_id"],
    )

    op.create_index(
        "ix_bulk_photo_imports_expires_at",
        "bulk_photo_imports",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bulk_photo_imports_expires_at",
        table_name="bulk_photo_imports",
    )

    op.drop_index(
        "ix_bulk_photo_imports_user_id",
        table_name="bulk_photo_imports",
    )

    op.drop_index(
        "ix_bulk_photo_imports_school_id",
        table_name="bulk_photo_imports",
    )

    op.drop_table("bulk_photo_imports")
