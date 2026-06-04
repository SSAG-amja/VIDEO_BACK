"""create recommendations table

Revision ID: d4e5f6a7b8c9
Revises: ca47ba289c35
Create Date: 2026-06-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "ca47ba289c35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = inspector.get_table_names()

    if "recommendations" not in table_names:
        op.create_table(
            "recommendations",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("movie_id", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=True),
            sa.Column("source_scores", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id", "movie_id"),
            sa.UniqueConstraint("user_id", "rank", name="uq_recommendations_user_rank"),
        )
    else:
        columns = {column["name"] for column in inspector.get_columns("recommendations")}
        if "source_scores" not in columns:
            op.add_column(
                "recommendations",
                sa.Column("source_scores", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            )

    existing_indexes = {index["name"] for index in inspector.get_indexes("recommendations")}
    if "ix_recommendations_user_id" not in existing_indexes:
        op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"])
    if "ix_recommendations_movie_id" not in existing_indexes:
        op.create_index("ix_recommendations_movie_id", "recommendations", ["movie_id"])
    if "ix_recommendations_user_rank" not in existing_indexes:
        op.create_index("ix_recommendations_user_rank", "recommendations", ["user_id", "rank"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "recommendations" not in inspector.get_table_names():
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("recommendations")}
    if "ix_recommendations_user_rank" in existing_indexes:
        op.drop_index("ix_recommendations_user_rank", table_name="recommendations")
    if "ix_recommendations_movie_id" in existing_indexes:
        op.drop_index("ix_recommendations_movie_id", table_name="recommendations")
    if "ix_recommendations_user_id" in existing_indexes:
        op.drop_index("ix_recommendations_user_id", table_name="recommendations")
    op.drop_table("recommendations")
