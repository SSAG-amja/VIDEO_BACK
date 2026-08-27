"""merge recommendation and community migration heads

Revision ID: c8d4e6f2a1b3
Revises: f1a2b3c4d5e6, 7937916adf23
Create Date: 2026-08-27
"""
from typing import Sequence, Union


revision: str = "c8d4e6f2a1b3"
down_revision: Union[str, Sequence[str], None] = (
    "f1a2b3c4d5e6",
    "7937916adf23",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
