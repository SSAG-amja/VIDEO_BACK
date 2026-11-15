"""scope precomputed recommendations by engine

Revision ID: e7f9a1b3c5d7
Revises: c5e7a9b1d3f5
Create Date: 2026-09-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f9a1b3c5d7"
down_revision: Union[str, Sequence[str], None] = "c5e7a9b1d3f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("engine", sa.String(length=20), nullable=False, server_default="v1"),
    )
    op.execute(
        """
        UPDATE recommendations
        SET engine = 'v3'
        WHERE source IN ('lightfm_v3', 'lightfm_v3_feature_only')
        """
    )
    op.drop_constraint("uq_recommendations_user_rank", "recommendations", type_="unique")
    op.drop_constraint("recommendations_pkey", "recommendations", type_="primary")
    op.create_primary_key(
        "recommendations_pkey",
        "recommendations",
        ["user_id", "movie_id", "engine"],
    )
    op.create_unique_constraint(
        "uq_recommendations_user_engine_rank",
        "recommendations",
        ["user_id", "engine", "rank"],
    )


def downgrade() -> None:
    # The old schema cannot retain parallel engine rows. Keep one deterministic
    # row for each conflicting user/movie or user/rank before restoring it.
    op.execute(
        """
        DELETE FROM recommendations target
        USING (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY user_id, movie_id
                       ORDER BY CASE engine WHEN 'v1' THEN 0 WHEN 'v3' THEN 1 ELSE 2 END,
                                created_at DESC NULLS LAST
                   ) AS ordinal
            FROM recommendations
        ) duplicates
        WHERE target.ctid = duplicates.ctid AND duplicates.ordinal > 1
        """
    )
    op.execute(
        """
        DELETE FROM recommendations target
        USING (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY user_id, rank
                       ORDER BY CASE engine WHEN 'v1' THEN 0 WHEN 'v3' THEN 1 ELSE 2 END,
                                created_at DESC NULLS LAST
                   ) AS ordinal
            FROM recommendations
        ) duplicates
        WHERE target.ctid = duplicates.ctid AND duplicates.ordinal > 1
        """
    )
    op.drop_constraint(
        "uq_recommendations_user_engine_rank",
        "recommendations",
        type_="unique",
    )
    op.drop_constraint("recommendations_pkey", "recommendations", type_="primary")
    op.drop_column("recommendations", "engine")
    op.create_primary_key(
        "recommendations_pkey",
        "recommendations",
        ["user_id", "movie_id"],
    )
    op.create_unique_constraint(
        "uq_recommendations_user_rank",
        "recommendations",
        ["user_id", "rank"],
    )
