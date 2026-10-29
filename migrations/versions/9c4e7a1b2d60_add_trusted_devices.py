"""Add trusted device records and MFA challenge preference."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9c4e7a1b2d60"
down_revision = "7f3a9c2d1e60"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user_mfa_challenges", sa.Column("remember_device", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=120), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_trusted_devices_user_id", "trusted_devices", ["user_id"])
    op.create_index("ix_trusted_devices_expires_at", "trusted_devices", ["expires_at"])
    op.create_index("ix_trusted_devices_user_active", "trusted_devices", ["user_id", "revoked_at", "expires_at"])

def downgrade():
    op.drop_index("ix_trusted_devices_user_active", table_name="trusted_devices")
    op.drop_index("ix_trusted_devices_expires_at", table_name="trusted_devices")
    op.drop_index("ix_trusted_devices_user_id", table_name="trusted_devices")
    op.drop_table("trusted_devices")
    op.drop_column("user_mfa_challenges", "remember_device")
