"""Enable deny-by-default RLS on security and trusted device tables.

Revision ID: e2f8a6c1d930
Revises: d1c7ce923227
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f8a6c1d930"
down_revision: Union[str, Sequence[str], None] = "d1c7ce923227"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SECURITY_TABLES = (
    "security_events",
    "trusted_devices",
)


def upgrade() -> None:
    """Enable deny-by-default RLS on security-related tables."""

    connection = op.get_bind()

    # Verify the executing database role can safely operate
    # with RLS enabled.
    for table_name in SECURITY_TABLES:

        result = connection.execute(
            sa.text(
                """
                SELECT
                    (
                        r.rolsuper
                        OR r.rolbypassrls
                        OR pg_get_userbyid(c.relowner) = current_user
                    )
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n
                    ON n.oid = c.relnamespace
                CROSS JOIN pg_catalog.pg_roles AS r
                WHERE n.nspname = 'public'
                  AND c.relname = :table_name
                  AND c.relkind IN ('r', 'p')
                  AND r.rolname = current_user
                """
            ),
            {"table_name": table_name},
        ).scalar_one_or_none()

        if result is not True:
            raise RuntimeError(
                "Refusing to enable RLS: database role cannot "
                f"safely bypass RLS or own public.{table_name}"
            )

    # Enable RLS without creating permissive policies.
    for table_name in SECURITY_TABLES:
        op.execute(
            f'ALTER TABLE public."{table_name}" '
            "ENABLE ROW LEVEL SECURITY"
        )


def downgrade() -> None:
    """Restore the previous RLS configuration."""

    for table_name in reversed(SECURITY_TABLES):
        op.execute(
            f'ALTER TABLE public."{table_name}" '
            "DISABLE ROW LEVEL SECURITY"
        )