"""add post title

Revision ID: 8f1b3c9d2a4e
Revises: 7f2e4b9c1d10
Create Date: 2026-05-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8f1b3c9d2a4e"
down_revision: Union[str, Sequence[str], None] = "7f2e4b9c1d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("post_title", sa.String(length=255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("posts", "post_title")
