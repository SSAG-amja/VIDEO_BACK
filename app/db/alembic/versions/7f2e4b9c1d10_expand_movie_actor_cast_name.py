"""expand movie actor cast_name

Revision ID: 7f2e4b9c1d10
Revises: c3b7a6f2d1aa
Create Date: 2026-05-05 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f2e4b9c1d10"
down_revision: Union[str, Sequence[str], None] = "c3b7a6f2d1aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "movie_actors",
        "cast_name",
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "movie_actors",
        "cast_name",
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
