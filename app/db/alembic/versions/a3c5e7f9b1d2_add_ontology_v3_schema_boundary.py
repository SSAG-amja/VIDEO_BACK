"""add ontology v3 schema boundary and edge evidence

Revision ID: a3c5e7f9b1d2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    build_columns = {column["name"] for column in inspector.get_columns("ontology_builds")}

    if "engine_name" not in build_columns:
        op.add_column(
            "ontology_builds",
            sa.Column("engine_name", sa.String(length=30), server_default=sa.text("'v2'"), nullable=False),
        )
    if "schema_version" not in build_columns:
        op.add_column(
            "ontology_builds",
            sa.Column("schema_version", sa.String(length=50), server_default=sa.text("'v2'"), nullable=False),
        )
    if "source_manifest" not in build_columns:
        op.add_column(
            "ontology_builds",
            sa.Column("source_manifest", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        )
    if "evidence_count" not in build_columns:
        op.add_column(
            "ontology_builds",
            sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        )

    inspector = inspect(bind)
    build_unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("ontology_builds")
    }
    if "uq_ontology_builds_source_hash" in build_unique_constraints:
        op.drop_constraint("uq_ontology_builds_source_hash", "ontology_builds", type_="unique")
    if "uq_ontology_builds_engine_schema_source_hash" not in build_unique_constraints:
        op.create_unique_constraint(
            "uq_ontology_builds_engine_schema_source_hash",
            "ontology_builds",
            ["engine_name", "schema_version", "source_hash"],
        )

    build_indexes = {index["name"] for index in inspect(bind).get_indexes("ontology_builds")}
    if "uq_ontology_builds_active_engine_schema" not in build_indexes:
        op.create_index(
            "uq_ontology_builds_active_engine_schema",
            "ontology_builds",
            ["engine_name", "schema_version"],
            unique=True,
            postgresql_where=sa.text("is_active IS TRUE"),
        )
    if "idx_ontology_builds_engine_schema_status" not in build_indexes:
        op.create_index(
            "idx_ontology_builds_engine_schema_status",
            "ontology_builds",
            ["engine_name", "schema_version", "status", "started_at"],
        )

    edge_columns = {column["name"] for column in inspect(bind).get_columns("ontology_edges")}
    if "effective_strength" not in edge_columns:
        op.add_column("ontology_edges", sa.Column("effective_strength", sa.Float(), nullable=True))
        # Legacy V2 edges keep NULL. V3 builders write the aggregated value explicitly,
        # avoiding a full rewrite of the large shared edge table during migration.
    if "evidence_count" not in edge_columns:
        op.add_column(
            "ontology_edges",
            sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        )

    if "ontology_edge_evidence" not in inspect(bind).get_table_names():
        op.create_table(
            "ontology_edge_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("build_id", sa.Integer(), nullable=False),
            sa.Column("edge_id", sa.Integer(), nullable=False),
            sa.Column("evidence_type", sa.String(length=50), nullable=False),
            sa.Column("source_ref", sa.String(length=255), nullable=False),
            sa.Column("path", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
            sa.Column("raw_weight", sa.Float(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("effective_strength", sa.Float(), nullable=False),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("raw_weight >= 0 AND raw_weight <= 1", name="ck_ontology_evidence_raw_weight"),
            sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_ontology_evidence_confidence"),
            sa.CheckConstraint(
                "effective_strength >= 0 AND effective_strength <= 1",
                name="ck_ontology_evidence_effective_strength",
            ),
            sa.ForeignKeyConstraint(["build_id"], ["ontology_builds.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["edge_id"], ["ontology_edges.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "build_id",
                "edge_id",
                "evidence_type",
                "source_ref",
                name="uq_ontology_edge_evidence_build_edge_type_source",
            ),
        )

    evidence_indexes = {
        index["name"] for index in inspect(bind).get_indexes("ontology_edge_evidence")
    }
    if "idx_ontology_edge_evidence_build_edge" not in evidence_indexes:
        op.create_index(
            "idx_ontology_edge_evidence_build_edge",
            "ontology_edge_evidence",
            ["build_id", "edge_id"],
        )
    if "idx_ontology_edge_evidence_build_type" not in evidence_indexes:
        op.create_index(
            "idx_ontology_edge_evidence_build_type",
            "ontology_edge_evidence",
            ["build_id", "evidence_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "ontology_edge_evidence" in inspector.get_table_names():
        op.drop_table("ontology_edge_evidence")

    edge_columns = {column["name"] for column in inspect(bind).get_columns("ontology_edges")}
    if "evidence_count" in edge_columns:
        op.drop_column("ontology_edges", "evidence_count")
    if "effective_strength" in edge_columns:
        op.drop_column("ontology_edges", "effective_strength")

    build_indexes = {index["name"] for index in inspect(bind).get_indexes("ontology_builds")}
    if "idx_ontology_builds_engine_schema_status" in build_indexes:
        op.drop_index("idx_ontology_builds_engine_schema_status", table_name="ontology_builds")
    if "uq_ontology_builds_active_engine_schema" in build_indexes:
        op.drop_index("uq_ontology_builds_active_engine_schema", table_name="ontology_builds")

    build_constraints = {
        constraint["name"] for constraint in inspect(bind).get_unique_constraints("ontology_builds")
    }
    if "uq_ontology_builds_engine_schema_source_hash" in build_constraints:
        op.drop_constraint(
            "uq_ontology_builds_engine_schema_source_hash",
            "ontology_builds",
            type_="unique",
        )
    if "uq_ontology_builds_source_hash" not in build_constraints:
        op.create_unique_constraint(
            "uq_ontology_builds_source_hash",
            "ontology_builds",
            ["source_hash"],
        )

    build_columns = {column["name"] for column in inspect(bind).get_columns("ontology_builds")}
    for column_name in ("evidence_count", "source_manifest", "schema_version", "engine_name"):
        if column_name in build_columns:
            op.drop_column("ontology_builds", column_name)
