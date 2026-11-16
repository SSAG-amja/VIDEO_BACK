"""create overview semantic signals table

Revision ID: f1a2b3c4d5e6
Revises: e2f4a6b8c0d1
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e2f4a6b8c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    inspector = inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "movie_overview_semantic_signals" not in table_names:
        op.create_table(
            "movie_overview_semantic_signals",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("movie_id", sa.Integer(), nullable=False),
            sa.Column("signal_type", sa.String(length=30), nullable=False),
            sa.Column("signal_key", sa.String(length=80), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("matched_terms", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("overview_hash", sa.String(length=64), nullable=False),
            sa.Column("asset_version", sa.String(length=50), nullable=False),
            sa.Column("extractor_version", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "movie_id",
                "signal_type",
                "signal_key",
                "extractor_version",
                name="uq_overview_signals_movie_type_key_extractor",
            ),
        )

    _create_index_if_missing("movie_overview_semantic_signals", "idx_overview_signals_movie", ["movie_id"])
    _create_index_if_missing(
        "movie_overview_semantic_signals",
        "idx_overview_signals_type_key",
        ["signal_type", "signal_key"],
    )
    _create_index_if_missing(
        "movie_overview_semantic_signals",
        "idx_overview_signals_extractor_movie",
        ["extractor_version", "movie_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "movie_overview_semantic_signals" not in inspector.get_table_names():
        return
    for index in inspector.get_indexes("movie_overview_semantic_signals"):
        op.drop_index(index["name"], table_name="movie_overview_semantic_signals")
    op.drop_table("movie_overview_semantic_signals")
