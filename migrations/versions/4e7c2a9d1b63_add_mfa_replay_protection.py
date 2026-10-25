"""Add persistent MFA challenge consumption and TOTP replay protection.

Revision ID: 4e7c2a9d1b63
Revises: 9d567aa047aa
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4e7c2a9d1b63"
down_revision: Union[str, Sequence[str], None] = "9d567aa047aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_deny_by_default_rls(table_name: str) -> None:
    connection = op.get_bind()
    can_backend_bypass_rls = connection.exec_driver_sql(
        """
        SELECT r.rolsuper OR r.rolbypassrls
            OR pg_has_role(current_user, c.relowner, 'MEMBER')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles AS r ON r.rolname = current_user
        WHERE n.nspname = 'public' AND c.relname = %s
          AND c.relkind IN ('r', 'p')
        """, (table_name,),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError(
            "Refusing to enable RLS: configured backend database role cannot "
            f"bypass RLS for public.{table_name}"
        )
    op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.add_column(
        "user_mfa_secrets",
        sa.Column("last_totp_counter", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "user_mfa_challenges",
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index("ix_user_mfa_challenges_user_id", "user_mfa_challenges", ["user_id"])
    op.create_index("ix_user_mfa_challenges_expires_at", "user_mfa_challenges", ["expires_at"])
    _enable_deny_by_default_rls("user_mfa_challenges")


def downgrade() -> None:
    op.drop_index("ix_user_mfa_challenges_expires_at", table_name="user_mfa_challenges")
    op.drop_index("ix_user_mfa_challenges_user_id", table_name="user_mfa_challenges")
    op.drop_table("user_mfa_challenges")
    op.drop_column("user_mfa_secrets", "last_totp_counter")
