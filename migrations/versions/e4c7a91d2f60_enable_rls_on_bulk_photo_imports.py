"""Enable deny-by-default RLS on bulk photo import metadata.

Revision ID: e4c7a91d2f60
Revises: b8e2f14c9a70
Create Date: 2026-09-08 23:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e4c7a91d2f60"
down_revision: Union[str, Sequence[str], None] = "b8e2f14c9a70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        ("bulk_photo_imports",),
    ).scalar_one_or_none()
    if can_backend_bypass_rls is not True:
        raise RuntimeError(
            "Refusing to enable RLS: the configured backend database role "
            "cannot bypass RLS for public.bulk_photo_imports"
        )
    op.execute(
        'ALTER TABLE public."bulk_photo_imports" ENABLE ROW LEVEL SECURITY'
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE public."bulk_photo_imports" DISABLE ROW LEVEL SECURITY'
    )
