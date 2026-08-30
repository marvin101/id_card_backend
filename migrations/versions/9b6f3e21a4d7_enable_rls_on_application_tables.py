"""Deny direct Supabase Data API access to application tables.

Revision ID: 9b6f3e21a4d7
Revises: 8f31d6a09c42
Create Date: 2026-08-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "9b6f3e21a4d7"
down_revision: Union[str, Sequence[str], None] = "8f31d6a09c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


APPLICATION_TABLES = (
    "alembic_version",
    "academic_sessions",
    "classes",
    "schools",
    "sections",
    "user_school_access",
    "users",
    "students",
    "card_templates",
    "school_access_requests",
)


def upgrade() -> None:
    """Enable deny-by-default RLS without changing backend authorization."""
    connection = op.get_bind()

    # CampusID uses one direct PostgreSQL role for both Alembic and the
    # SQLAlchemy application. RLS is safe only when that role owns each table,
    # is a superuser, or has BYPASSRLS. Fail before changing anything if the
    # deployment credentials do not satisfy that invariant.
    for table_name in APPLICATION_TABLES:
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

    for table_name in APPLICATION_TABLES:
        op.execute(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Restore the pre-hardening RLS state."""
    for table_name in reversed(APPLICATION_TABLES):
        op.execute(f'ALTER TABLE public."{table_name}" DISABLE ROW LEVEL SECURITY')

