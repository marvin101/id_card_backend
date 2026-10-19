"""Add TOTP MFA secret and recovery-code tables.

Revision ID: a1b2c3d4e5f6
Revises: f3a6c2d9e814
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f3a6c2d9e814"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_deny_by_default_rls(table_name: str) -> None:
    connection = op.get_bind()
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


def upgrade() -> None:
    op.create_table(
        "user_mfa_secrets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_secret", sa.String(512), nullable=False),
        sa.Column("issuer_label", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_mfa_secrets_user_id", "user_mfa_secrets", ["user_id"])

    op.create_table(
        "user_mfa_recovery_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_user_id", "user_mfa_recovery_codes", ["user_id"]
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_expires_at",
        "user_mfa_recovery_codes",
        ["expires_at"],
    )

    _enable_deny_by_default_rls("user_mfa_secrets")
    _enable_deny_by_default_rls("user_mfa_recovery_codes")


def downgrade() -> None:
    op.drop_index(
        "ix_user_mfa_recovery_codes_expires_at", table_name="user_mfa_recovery_codes"
    )
    op.drop_index(
        "ix_user_mfa_recovery_codes_user_id", table_name="user_mfa_recovery_codes"
    )
    op.drop_table("user_mfa_recovery_codes")
    op.drop_index("ix_user_mfa_secrets_user_id", table_name="user_mfa_secrets")
    op.drop_table("user_mfa_secrets")