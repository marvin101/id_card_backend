"""Add device metadata to authentication sessions.

Revision ID: 7f3a9c2d1e60
Revises: 4e7c2a9d1b63
"""
from alembic import op
import sqlalchemy as sa

revision = "7f3a9c2d1e60"
down_revision = "4e7c2a9d1b63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("auth_sessions", sa.Column("ip_address", sa.String(length=45), nullable=True))
    op.add_column("auth_sessions", sa.Column("user_agent", sa.String(length=500), nullable=True))
    op.add_column("auth_sessions", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("auth_sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE auth_sessions SET created_at = COALESCE(created_at, now()), last_seen_at = COALESCE(last_seen_at, now())")
    op.alter_column("auth_sessions", "created_at", nullable=False)
    op.alter_column("auth_sessions", "last_seen_at", nullable=False)


def downgrade():
    op.drop_column("auth_sessions", "last_seen_at")
    op.drop_column("auth_sessions", "created_at")
    op.drop_column("auth_sessions", "user_agent")
    op.drop_column("auth_sessions", "ip_address")
