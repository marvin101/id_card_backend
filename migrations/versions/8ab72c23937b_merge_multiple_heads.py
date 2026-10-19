"""merge multiple heads

Revision ID: 8ab72c23937b
Revises: a1b2c3d4e5f6, b7e1c2d3f4a5
Create Date: 2026-10-19 16:15:38.711799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8ab72c23937b'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'b7e1c2d3f4a5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
