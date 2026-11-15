"""Add encrypted transactional email outbox.

Revision ID: a8d4c2e91f60
Revises: f7c2a91d6e40
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8d4c2e91f60"
down_revision = "f7c2a91d6e40"
branch_labels = None
depends_on = None


def _enable_deny_by_default_rls(table_name: str) -> None:
    connection = op.get_bind()
    can_backend_bypass_rls = connection.exec_driver_sql(
        """
        SELECT r.rolsuper OR r.rolbypassrls
            OR pg_has_role(current_user, c.relowner, 'MEMBER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
        WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind IN ('r', 'p')
        """, (table_name,),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError(
            "Refusing to enable RLS: configured backend role cannot bypass RLS "
            f"for public.{table_name}"
        )
    op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql"),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("message_type", sa.String(50), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(100), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'processing', 'sent', 'failed')", name="ck_email_outbox_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_email_outbox_attempts"),
        sa.PrimaryKeyConstraint("id", name=op.f("email_outbox_pkey")),
    )
    op.create_index("ix_email_outbox_ready", "email_outbox", ["status", "available_at"])
    op.create_index("ix_email_outbox_locked_at", "email_outbox", ["locked_at"])
    _enable_deny_by_default_rls("email_outbox")


def downgrade() -> None:
    op.drop_index("ix_email_outbox_locked_at", table_name="email_outbox")
    op.drop_index("ix_email_outbox_ready", table_name="email_outbox")
    op.drop_table("email_outbox")
