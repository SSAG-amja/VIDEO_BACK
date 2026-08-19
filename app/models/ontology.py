from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import relationship

from app.db.base import Base


class OntologyBuild(Base):
    __tablename__ = "ontology_builds"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("false"))
    source_hash = Column(String(128), nullable=False, unique=True)
    node_count = Column(Integer, nullable=False, server_default=text("0"))
    edge_count = Column(Integer, nullable=False, server_default=text("0"))
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    finished_at = Column(DateTime)
    error_message = Column(Text)
    properties = Column(JSON)

    nodes = relationship("OntologyNode", back_populates="build", cascade="all, delete-orphan")
    edges = relationship("OntologyEdge", back_populates="build", cascade="all, delete-orphan")


class OntologyNode(Base):
    __tablename__ = "ontology_nodes"
    __table_args__ = (
        UniqueConstraint("build_id", "node_type", "ref_id", name="uq_ontology_nodes_build_type_ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
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
    )

    id = Column(Integer, primary_key=True, index=True)
    build_id = Column(Integer, ForeignKey("ontology_builds.id", ondelete="CASCADE"), nullable=False)
    source_node_id = Column(Integer, ForeignKey("ontology_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("ontology_nodes.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(80), nullable=False)
    weight = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    source = Column(String(50), nullable=False)
    properties = Column(JSON)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    build = relationship("OntologyBuild", back_populates="edges")


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
    )

    id = Column(Integer, primary_key=True, index=True)
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
