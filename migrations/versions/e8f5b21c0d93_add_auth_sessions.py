"""Add durable, rotating workday authentication sessions.

Revision ID: e8f5b21c0d93
Revises: d7e4a10b9c82
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e8f5b21c0d93"
down_revision = "d7e4a10b9c82"
branch_labels = None
depends_on = None


def _enable_deny_by_default_rls(table_names: tuple[str, ...]) -> None:
    connection = op.get_bind()
    for table_name in table_names:
        can_backend_bypass_rls = connection.exec_driver_sql(
            """
            SELECT r.rolsuper
                OR r.rolbypassrls
                OR pg_has_role(current_user, c.relowner, 'MEMBER')
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
            WHERE n.nspname = 'public'
              AND c.relname = %s
              AND c.relkind IN ('r', 'p')
            """,
            (table_name,),
        ).scalar_one_or_none()
        if can_backend_bypass_rls is not True:
            raise RuntimeError(
                "Refusing to enable RLS: the configured backend database "
                f"role cannot bypass RLS for public.{table_name}"
            )
        op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')


def upgrade():
    op.create_table("auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    _enable_deny_by_default_rls(("auth_sessions",))


def downgrade():
    # Logging out all session-backed clients is an intentional downgrade effect.
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
