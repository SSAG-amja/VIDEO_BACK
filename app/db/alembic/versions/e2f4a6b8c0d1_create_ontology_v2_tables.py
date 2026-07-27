"""create ontology v2 recommendation tables

Revision ID: e2f4a6b8c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "e2f4a6b8c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
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

    if "ontology_builds" not in table_names:
        op.create_table(
            "ontology_builds",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("source_hash", sa.String(length=128), nullable=False),
            sa.Column("node_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("edge_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_hash", name="uq_ontology_builds_source_hash"),
        )
    _create_index_if_missing("ontology_builds", "idx_ontology_builds_active", ["is_active"])
    _create_index_if_missing("ontology_builds", "idx_ontology_builds_status_started_at", ["status", "started_at"])

    table_names = set(inspector.get_table_names())
    if "ontology_nodes" not in table_names:
        op.create_table(
            "ontology_nodes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("build_id", sa.Integer(), nullable=False),
            sa.Column("node_type", sa.String(length=50), nullable=False),
            sa.Column("ref_id", sa.String(length=128), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("label_ko", sa.String(length=255), nullable=True),
            sa.Column("label_en", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("source_table", sa.String(length=100), nullable=True),
            sa.Column("confidence", sa.Float(), server_default=sa.text("1.0"), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["build_id"], ["ontology_builds.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("build_id", "node_type", "ref_id", name="uq_ontology_nodes_build_type_ref"),
        )
    _create_index_if_missing("ontology_nodes", "idx_ontology_nodes_build_type", ["build_id", "node_type"])
    _create_index_if_missing("ontology_nodes", "idx_ontology_nodes_build_type_ref", ["build_id", "node_type", "ref_id"])
    _create_index_if_missing("ontology_nodes", "idx_ontology_nodes_build_active", ["build_id", "is_active"])

    table_names = set(inspector.get_table_names())
    if "ontology_edges" not in table_names:
        op.create_table(
            "ontology_edges",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("build_id", sa.Integer(), nullable=False),
            sa.Column("source_node_id", sa.Integer(), nullable=False),
            sa.Column("target_node_id", sa.Integer(), nullable=False),
            sa.Column("relation_type", sa.String(length=80), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["build_id"], ["ontology_builds.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_node_id"], ["ontology_nodes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_node_id"], ["ontology_nodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "build_id",
                "source_node_id",
                "target_node_id",
                "relation_type",
                name="uq_ontology_edges_build_source_target_relation",
            ),
        )
    _create_index_if_missing("ontology_edges", "idx_ontology_edges_build_source", ["build_id", "source_node_id"])
    _create_index_if_missing("ontology_edges", "idx_ontology_edges_build_target", ["build_id", "target_node_id"])
    _create_index_if_missing("ontology_edges", "idx_ontology_edges_source_node", ["source_node_id"])
    _create_index_if_missing("ontology_edges", "idx_ontology_edges_target_node", ["target_node_id"])
    _create_index_if_missing("ontology_edges", "idx_ontology_edges_build_relation", ["build_id", "relation_type"])
    _create_index_if_missing(
        "ontology_edges",
        "idx_ontology_edges_build_relation_source",
        ["build_id", "relation_type", "source_node_id"],
    )
    _create_index_if_missing(
        "ontology_edges",
        "idx_ontology_edges_build_relation_target",
        ["build_id", "relation_type", "target_node_id"],
    )

    table_names = set(inspector.get_table_names())
    if "recommendation_runs" not in table_names:
        op.create_table(
            "recommendation_runs",
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("engine", sa.String(length=50), nullable=False),
            sa.Column("engine_version", sa.String(length=50), nullable=False),
            sa.Column("ontology_build_id", sa.Integer(), nullable=True),
            sa.Column("run_type", sa.String(length=30), nullable=False),
            sa.Column("config_snapshot", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("elapsed_time", sa.Float(), nullable=True),
            sa.Column("processed_user_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("generated_candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("source_counts", sa.JSON(), nullable=True),
            sa.Column("fallback_ratio", sa.Float(), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["ontology_build_id"], ["ontology_builds.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("run_id"),
        )
    _create_index_if_missing("recommendation_runs", "idx_recommendation_runs_engine_started", ["engine", "started_at"])
    _create_index_if_missing("recommendation_runs", "idx_recommendation_runs_type_started", ["run_type", "started_at"])
    _create_index_if_missing("recommendation_runs", "idx_recommendation_runs_build", ["ontology_build_id"])
    _create_index_if_missing("recommendation_runs", "idx_recommendation_runs_status_started", ["status", "started_at"])

    table_names = set(inspector.get_table_names())
    if "ontology_recommendations" not in table_names:
        op.create_table(
            "ontology_recommendations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("feed_session_key", sa.String(length=128), nullable=True),
            sa.Column("refresh_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("movie_id", sa.Integer(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("source_scores", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column("explanation_tags", sa.JSON(), nullable=True),
            sa.Column("ontology_build_id", sa.Integer(), nullable=True),
            sa.Column("engine_version", sa.String(length=50), nullable=False),
            sa.Column("candidate_stage", sa.String(length=30), nullable=False),
            sa.Column("exposure_state", sa.String(length=30), server_default=sa.text("'not_exposed'"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ontology_build_id"], ["ontology_builds.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["recommendation_runs.run_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", "candidate_stage", "movie_id", name="uq_ontology_recs_request_stage_movie"),
        )
    _create_index_if_missing("ontology_recommendations", "idx_ontology_recs_user_created", ["user_id", "created_at"])
    _create_index_if_missing("ontology_recommendations", "idx_ontology_recs_session_refresh", ["feed_session_key", "refresh_count"])
    _create_index_if_missing("ontology_recommendations", "idx_ontology_recs_run", ["run_id"])
    _create_index_if_missing("ontology_recommendations", "idx_ontology_recs_build", ["ontology_build_id"])
    _create_index_if_missing("ontology_recommendations", "idx_ontology_recs_request_rank", ["request_id", "rank"])

    table_names = set(inspector.get_table_names())
    if "recommendation_feed_events" not in table_names:
        op.create_table(
            "recommendation_feed_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("feed_session_key", sa.String(length=128), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("movie_id", sa.Integer(), nullable=False),
            sa.Column("recommendation_id", sa.Integer(), nullable=True),
            sa.Column("ontology_build_id", sa.Integer(), nullable=True),
            sa.Column("engine", sa.String(length=50), nullable=False),
            sa.Column("engine_version", sa.String(length=50), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("rank_at_event", sa.Integer(), nullable=True),
            sa.Column("score_at_event", sa.Float(), nullable=True),
            sa.Column("dwell_ms", sa.Integer(), nullable=True),
            sa.Column("refresh_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("source_scores_snapshot", sa.JSON(), nullable=True),
            sa.Column("profile_snapshot", sa.JSON(), nullable=True),
            sa.Column("context", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ontology_build_id"], ["ontology_builds.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["recommendation_id"], ["ontology_recommendations.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_recommendation_feed_events_event_id"),
        )
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_user_created", ["user_id", "created_at"])
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_session_created", ["feed_session_key", "created_at"])
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_request", ["request_id"])
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_movie_created", ["movie_id", "created_at"])
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_type_created", ["event_type", "created_at"])
    _create_index_if_missing("recommendation_feed_events", "idx_rec_feed_events_recommendation", ["recommendation_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    for table_name in (
        "recommendation_feed_events",
        "ontology_recommendations",
        "recommendation_runs",
        "ontology_edges",
        "ontology_nodes",
        "ontology_builds",
    ):
        if table_name not in table_names:
            continue
        for index in inspector.get_indexes(table_name):
            op.drop_index(index["name"], table_name=table_name)
        op.drop_table(table_name)
