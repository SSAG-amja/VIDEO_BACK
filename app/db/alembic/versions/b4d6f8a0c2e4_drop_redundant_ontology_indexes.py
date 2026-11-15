"""drop redundant ontology indexes

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "b4d6f8a0c2e4"
down_revision: Union[str, Sequence[str], None] = "a3c5e7f9b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REDUNDANT_INDEXES = {
    "ontology_nodes": (
        "idx_ontology_nodes_build_type_ref",
        "idx_ontology_nodes_build_type",
    ),
    "ontology_edges": (
        "idx_ontology_edges_build_source",
        "idx_ontology_edges_build_relation",
    ),
    "ontology_edge_evidence": (
        "idx_ontology_edge_evidence_build_edge",
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, index_names in REDUNDANT_INDEXES.items():
        existing_indexes = {index["name"] for index in inspect(bind).get_indexes(table_name)}
        for index_name in index_names:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {
        table_name: {index["name"] for index in inspect(bind).get_indexes(table_name)}
        for table_name in REDUNDANT_INDEXES
    }
    indexes = (
        (
            "ontology_nodes",
            "idx_ontology_nodes_build_type_ref",
            ["build_id", "node_type", "ref_id"],
        ),
        ("ontology_nodes", "idx_ontology_nodes_build_type", ["build_id", "node_type"]),
        (
            "ontology_edges",
            "idx_ontology_edges_build_source",
            ["build_id", "source_node_id"],
        ),
        (
            "ontology_edges",
            "idx_ontology_edges_build_relation",
            ["build_id", "relation_type"],
        ),
        (
            "ontology_edge_evidence",
            "idx_ontology_edge_evidence_build_edge",
            ["build_id", "edge_id"],
        ),
    )
    for table_name, index_name, columns in indexes:
        if index_name not in existing[table_name]:
            op.create_index(index_name, table_name, columns)
