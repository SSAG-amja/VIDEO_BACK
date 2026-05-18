"""make people name_ko nullable

Revision ID: 1cb3f5e8d2aa
Revises: 7f2e4b9c1d10
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1cb3f5e8d2aa"
down_revision: Union[str, Sequence[str], None] = "7f2e4b9c1d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "people",
        "name_ko",
        existing_type=sa.String(length=100),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "people",
        "name_ko",
        existing_type=sa.String(length=100),
        nullable=False,
    )
