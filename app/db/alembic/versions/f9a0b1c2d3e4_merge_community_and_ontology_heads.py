"""merge community and ontology heads

Revision ID: f9a0b1c2d3e4
Revises: 7937916adf23, e7f9a1b3c5d7
Create Date: 2026-11-15 22:52:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, Sequence[str], None] = ('7937916adf23', 'e7f9a1b3c5d7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
