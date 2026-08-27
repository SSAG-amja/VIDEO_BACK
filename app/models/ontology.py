from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class OntologyBuild(Base):
    __tablename__ = "ontology_builds"
    __table_args__ = (
        UniqueConstraint(
            "engine_name",
            "schema_version",
            "source_hash",
            name="uq_ontology_builds_engine_schema_source_hash",
        ),
        Index(
            "uq_ontology_builds_active_engine_schema",
            "engine_name",
            "schema_version",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
        ),
        Index("idx_ontology_builds_active", "is_active"),
        Index("idx_ontology_builds_status_started_at", "status", "started_at"),
        Index(
            "idx_ontology_builds_engine_schema_status",
            "engine_name",
            "schema_version",
            "status",
            "started_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    engine_name = Column(String(30), nullable=False, server_default=text("'v2'"))
    schema_version = Column(String(50), nullable=False, server_default=text("'v2'"))
    version = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("false"))
    source_hash = Column(String(128), nullable=False)
    source_manifest = Column(JSON, nullable=False, server_default=text("'{}'"))
    node_count = Column(Integer, nullable=False, server_default=text("0"))
    edge_count = Column(Integer, nullable=False, server_default=text("0"))
    evidence_count = Column(Integer, nullable=False, server_default=text("0"))
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime)
    error_message = Column(Text)
    properties = Column(JSON)

    nodes = relationship("OntologyNode", back_populates="build", cascade="all, delete-orphan")
    edges = relationship("OntologyEdge", back_populates="build", cascade="all, delete-orphan")
    edge_evidences = relationship("OntologyEdgeEvidence", back_populates="build", cascade="all, delete-orphan")


class OntologyNode(Base):
    __tablename__ = "ontology_nodes"
    __table_args__ = (
        UniqueConstraint("build_id", "node_type", "ref_id", name="uq_ontology_nodes_build_type_ref"),
        Index("idx_ontology_nodes_build_active", "build_id", "is_active"),
    )

    id = Column(Integer, primary_key=True)
    build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="CASCADE"), nullable=False)
    node_type = Column(String(50), nullable=False)
    ref_id = Column(String(128), nullable=False)
    label = Column(String(255), nullable=False)
    label_ko = Column(String(255))
    label_en = Column(String(255))
    source = Column(String(50), nullable=False)
    source_table = Column(String(100))
    confidence = Column(Float, nullable=False, server_default=text("1.0"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    properties = Column(JSON)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    build = relationship("OntologyBuild", back_populates="nodes")


class OntologyEdge(Base):
    __tablename__ = "ontology_edges"
    __table_args__ = (
        UniqueConstraint(
            "build_id",
            "source_node_id",
            "target_node_id",
            "relation_type",
            name="uq_ontology_edges_build_source_target_relation",
        ),
        Index("idx_ontology_edges_source_node", "source_node_id"),
        Index("idx_ontology_edges_target_node", "target_node_id"),
        Index(
            "idx_ontology_edges_build_relation_source",
            "build_id",
            "relation_type",
            "source_node_id",
        ),
        Index(
            "idx_ontology_edges_build_relation_target",
            "build_id",
            "relation_type",
            "target_node_id",
        ),
        Index("idx_ontology_edges_build_target", "build_id", "target_node_id"),
    )

    id = Column(Integer, primary_key=True)
    build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="CASCADE"), nullable=False)
    source_node_id = Column(Integer, ForeignKey("ontology_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("ontology_nodes.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(80), nullable=False)
    weight = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    effective_strength = Column(Float)
    evidence_count = Column(Integer, nullable=False, server_default=text("0"))
    source = Column(String(50), nullable=False)
    properties = Column(JSON)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    build = relationship("OntologyBuild", back_populates="edges")
    evidences = relationship("OntologyEdgeEvidence", back_populates="edge", cascade="all, delete-orphan")


class OntologyEdgeEvidence(Base):
    __tablename__ = "ontology_edge_evidence"
    __table_args__ = (
        UniqueConstraint(
            "build_id",
            "edge_id",
            "evidence_type",
            "source_ref",
            name="uq_ontology_edge_evidence_build_edge_type_source",
        ),
        CheckConstraint(
            "raw_weight >= 0 AND raw_weight <= 1",
            name="ck_ontology_evidence_raw_weight",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ontology_evidence_confidence",
        ),
        CheckConstraint(
            "effective_strength >= 0 AND effective_strength <= 1",
            name="ck_ontology_evidence_effective_strength",
        ),
        Index("idx_ontology_edge_evidence_edge_id", "edge_id"),
        Index("idx_ontology_edge_evidence_build_type", "build_id", "evidence_type"),
    )

    id = Column(Integer, primary_key=True)
    build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="CASCADE"), nullable=False)
    edge_id = Column(Integer, ForeignKey("ontology_edges.id", ondelete="CASCADE"), nullable=False)
    evidence_type = Column(String(50), nullable=False)
    source_ref = Column(String(255), nullable=False)
    path = Column(JSON, nullable=False, server_default=text("'[]'"))
    raw_weight = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    effective_strength = Column(Float, nullable=False)
    properties = Column(JSON)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    build = relationship("OntologyBuild", back_populates="edge_evidences")
    edge = relationship("OntologyEdge", back_populates="evidences")


class MovieOverviewSemanticSignal(Base):
    __tablename__ = "movie_overview_semantic_signals"
    __table_args__ = (
        UniqueConstraint(
            "movie_id",
            "signal_type",
            "signal_key",
            "extractor_version",
            name="uq_overview_signals_movie_type_key_extractor",
        ),
        Index("idx_overview_signals_movie", "movie_id"),
        Index("idx_overview_signals_type_key", "signal_type", "signal_key"),
        Index(
            "idx_overview_signals_extractor_movie",
            "extractor_version",
            "movie_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    signal_type = Column(String(30), nullable=False)
    signal_key = Column(String(80), nullable=False)
    weight = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    matched_terms = Column(JSON, nullable=False, server_default=text("'[]'"))
    overview_hash = Column(String(64), nullable=False)
    asset_version = Column(String(50), nullable=False)
    extractor_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
