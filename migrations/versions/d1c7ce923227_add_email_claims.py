"""add email claims

Revision ID: d1c7ce923227
Revises: 03fa29ac075e
Create Date: 2026-11-09 12:32:49.546978

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1c7ce923227"
down_revision: Union[str, Sequence[str], None] = "03fa29ac075e"
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

    op.execute(
        f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'
    )


def upgrade() -> None:
    """Create email claims and backfill existing email addresses."""

    op.create_table(
        "email_claims",
        sa.Column("email_key", sa.String(length=150), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("claim_type", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "claim_type IN ('current', 'pending')",
            name="ck_email_claims_claim_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("email_claims_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "email_key",
            name=op.f("email_claims_pkey"),
        ),
    )

    op.create_index(
        op.f("ix_email_claims_user_id"),
        "email_claims",
        ["user_id"],
        unique=False,
    )

    op.create_index(
        "uq_email_claims_user_current",
        "email_claims",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("claim_type = 'current'"),
    )

    op.create_index(
        "uq_email_claims_user_pending",
        "email_claims",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("claim_type = 'pending'"),
    )

    # --------------------------------------------------
    # Detect duplicate normalized addresses.
    # Abort instead of silently assigning ownership.
    # --------------------------------------------------

    duplicate_query = sa.text(
        """
        WITH email_addresses AS (
            SELECT
                id AS user_id,
                'current' AS claim_type,
                lower(btrim(email)) AS email_key
            FROM users
            WHERE email IS NOT NULL
              AND btrim(email) <> ''

            UNION ALL

            SELECT
                id AS user_id,
                'pending' AS claim_type,
                lower(btrim(pending_email)) AS email_key
            FROM users
            WHERE pending_email IS NOT NULL
              AND btrim(pending_email) <> ''
        )
        SELECT
            email_key,
            COUNT(*) AS claim_count,
            ARRAY_AGG(user_id) AS user_ids,
            ARRAY_AGG(claim_type) AS claim_types
        FROM email_addresses
        GROUP BY email_key
        HAVING COUNT(*) > 1
        ORDER BY email_key
        """
    )

    duplicates = op.get_bind().execute(duplicate_query).all()

    if duplicates:
        details = "; ".join(
            f"{row.email_key}: users={row.user_ids}, "
            f"types={row.claim_types}"
            for row in duplicates
        )

        raise RuntimeError(
            "Duplicate email addresses detected. "
            "Resolve these conflicts before migrating: "
            f"{details}"
        )

    # --------------------------------------------------
    # Backfill existing current and pending addresses.
    # Existing verification status is NOT modified.
    # --------------------------------------------------

    op.execute(
        """
        INSERT INTO email_claims (
            email_key,
            user_id,
            claim_type
        )
        SELECT
            lower(btrim(email)),
            id,
            'current'
        FROM users
        WHERE email IS NOT NULL
          AND btrim(email) <> ''

        UNION ALL

        SELECT
            lower(btrim(pending_email)),
            id,
            'pending'
        FROM users
        WHERE pending_email IS NOT NULL
          AND btrim(pending_email) <> ''
        """
    )

    # --------------------------------------------------
    # Enable RLS.
    # --------------------------------------------------

    _enable_deny_by_default_rls("email_claims")


def downgrade() -> None:
    """Remove email claims."""

    op.drop_index(
        "uq_email_claims_user_pending",
        table_name="email_claims",
    )

    op.drop_index(
        "uq_email_claims_user_current",
        table_name="email_claims",
    )

    op.drop_index(
        op.f("ix_email_claims_user_id"),
        table_name="email_claims",
    )

    op.drop_table("email_claims")