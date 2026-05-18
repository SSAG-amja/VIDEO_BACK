"""expand keyword and movie title columns

Revision ID: c3b7a6f2d1aa
Revises: 519b0ad34f2b
Create Date: 2026-05-05 16:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3b7a6f2d1aa'
down_revision: Union[str, Sequence[str], None] = '519b0ad34f2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'keywords',
        'name',
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        'movies',
        'title',
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'movies',
        'title',
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        'keywords',
        'name',
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
