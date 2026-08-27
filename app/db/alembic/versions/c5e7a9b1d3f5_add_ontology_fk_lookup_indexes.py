"""add ontology foreign key lookup indexes

Revision ID: c5e7a9b1d3f5
Revises: b4d6f8a0c2e4
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect


revision: str = "c5e7a9b1d3f5"
down_revision: Union[str, Sequence[str], None] = "b4d6f8a0c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ontology_edge_evidence"
INDEX_NAME = "idx_ontology_edge_evidence_edge_id"


def upgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {
        index["name"] for index in inspect(bind).get_indexes(TABLE_NAME)
    }
    if INDEX_NAME not in existing_indexes:
        op.create_index(INDEX_NAME, TABLE_NAME, ["edge_id"])


def downgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {
        index["name"] for index in inspect(bind).get_indexes(TABLE_NAME)
    }
    if INDEX_NAME in existing_indexes:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
