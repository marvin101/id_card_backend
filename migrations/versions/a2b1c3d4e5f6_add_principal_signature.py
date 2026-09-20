"""Add school principal signature storage path.

Revision ID: a2b1c3d4e5f6
Revises: e8f5b21c0d93
"""
from alembic import op
import sqlalchemy as sa

revision = "a2b1c3d4e5f6"
down_revision = "e8f5b21c0d93"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("schools", sa.Column("principal_signature_path", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("schools", "principal_signature_path")
