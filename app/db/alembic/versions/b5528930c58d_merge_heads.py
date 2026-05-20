"""merge heads

Revision ID: b5528930c58d
Revises: 1cb3f5e8d2aa, 8f1b3c9d2a4e
Create Date: 2026-05-18 03:40:39.609657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5528930c58d'
down_revision: Union[str, Sequence[str], None] = ('1cb3f5e8d2aa', '8f1b3c9d2a4e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
