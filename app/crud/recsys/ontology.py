from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.ontology import OntologyBuild, OntologyEdge, OntologyEdgeEvidence, OntologyNode


def get_active_build(
    db: Session,
    *,
    engine_name: str = "v2",
    schema_version: str = "v2",
) -> OntologyBuild | None:
    stmt = (
        select(OntologyBuild)
        .where(
            OntologyBuild.engine_name == engine_name,
            OntologyBuild.schema_version == schema_version,
            OntologyBuild.is_active.is_(True),
            OntologyBuild.status == "success",
        )
        .order_by(OntologyBuild.started_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_build_by_source_hash(
    db: Session,
    source_hash: str,
    *,
    engine_name: str = "v2",
    schema_version: str = "v2",
) -> OntologyBuild | None:
    stmt = (
        select(OntologyBuild)
        .where(
            OntologyBuild.engine_name == engine_name,
            OntologyBuild.schema_version == schema_version,
            OntologyBuild.source_hash == source_hash,
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_build(
    db: Session,
    *,
    version: str,
    source_hash: str,
    engine_name: str = "v2",
    schema_version: str = "v2",
    source_manifest: dict | None = None,
    properties: dict | None = None,
) -> OntologyBuild:
    build = OntologyBuild(
        engine_name=engine_name,
        schema_version=schema_version,
        version=version,
        status="running",
        is_active=False,
        source_hash=source_hash,
        source_manifest=source_manifest or {},
        properties=properties,
    )
    db.add(build)
    db.flush()
    return build


def mark_build_success(
    db: Session,
    build: OntologyBuild,
    *,
    node_count: int,
    edge_count: int,
    evidence_count: int = 0,
    activate: bool = True,
) -> OntologyBuild:
    build.status = "success"
    build.is_active = False
    build.node_count = node_count
    build.edge_count = edge_count
    build.evidence_count = evidence_count
    build.finished_at = func.now()
    build.error_message = None
    if activate:
        activate_build(db, build)
    db.flush()
    return build


def activate_build(db: Session, build: OntologyBuild) -> OntologyBuild:
    if build.status != "success":
        raise ValueError(f"only successful ontology builds can be activated build_id={build.id}")
    db.execute(
        update(OntologyBuild)
        .where(
            OntologyBuild.engine_name == build.engine_name,
            OntologyBuild.schema_version == build.schema_version,
            OntologyBuild.is_active.is_(True),
            OntologyBuild.id != build.id,
        )
        .values(is_active=False, status="inactive")
    )
    build.status = "success"
    build.is_active = True
    db.flush()
    return build


def mark_build_failed(db: Session, build: OntologyBuild, *, error_message: str) -> OntologyBuild:
    build.status = "failed"
    build.finished_at = func.now()
    build.error_message = error_message
    db.flush()
    return build


def add_nodes(db: Session, nodes: list[OntologyNode]) -> None:
    db.add_all(nodes)
    db.flush()


def add_edges(db: Session, edges: list[OntologyEdge]) -> None:
    db.add_all(edges)
    db.flush()


def add_edge_evidence(db: Session, evidences: list[OntologyEdgeEvidence]) -> None:
    if not evidences:
        return
    edge_ids = {evidence.edge_id for evidence in evidences}
    edge_build_by_id = dict(
        db.execute(
            select(OntologyEdge.id, OntologyEdge.build_id).where(OntologyEdge.id.in_(edge_ids))
        ).all()
    )
    if edge_ids != set(edge_build_by_id):
        missing_edge_ids = sorted(edge_ids - set(edge_build_by_id))
        raise ValueError(f"ontology evidence references missing edges edge_ids={missing_edge_ids}")
    mismatches = sorted(
        (evidence.edge_id, evidence.build_id, edge_build_by_id[evidence.edge_id])
        for evidence in evidences
        if edge_build_by_id[evidence.edge_id] != evidence.build_id
    )
    if mismatches:
        raise ValueError(
            "ontology evidence build_id must match its edge build_id "
            f"mismatches={mismatches}"
        )
    db.add_all(evidences)
    db.flush()


def list_edge_evidence(
    db: Session,
    *,
    build_id: int,
    edge_ids: set[int],
    evidence_types: set[str] | None = None,
    limit: int = 5000,
) -> list[OntologyEdgeEvidence]:
    if not edge_ids:
        return []
    stmt = select(OntologyEdgeEvidence).where(
        OntologyEdgeEvidence.build_id == build_id,
        OntologyEdgeEvidence.edge_id.in_(edge_ids),
    )
    if evidence_types:
        stmt = stmt.where(OntologyEdgeEvidence.evidence_type.in_(evidence_types))
    return list(db.execute(stmt.limit(limit)).scalars().all())


def list_nodes_by_type(db: Session, *, build_id: int, node_type: str, limit: int = 1000) -> list[OntologyNode]:
    stmt = (
        select(OntologyNode)
        .where(
            OntologyNode.build_id == build_id,
            OntologyNode.node_type == node_type,
            OntologyNode.is_active.is_(True),
        )
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def list_edges_from_node(
    db: Session,
    *,
    build_id: int,
    source_node_id: int,
    relation_types: set[str] | None = None,
    limit: int = 1000,
) -> list[OntologyEdge]:
    stmt = select(OntologyEdge).where(
        OntologyEdge.build_id == build_id,
        OntologyEdge.source_node_id == source_node_id,
    )
    if relation_types:
        stmt = stmt.where(OntologyEdge.relation_type.in_(relation_types))
    return list(db.execute(stmt.limit(limit)).scalars().all())


def list_edges_to_node(
    db: Session,
    *,
    build_id: int,
    target_node_id: int,
    relation_types: set[str] | None = None,
    limit: int = 1000,
) -> list[OntologyEdge]:
    stmt = select(OntologyEdge).where(
        OntologyEdge.build_id == build_id,
        OntologyEdge.target_node_id == target_node_id,
    )
    if relation_types:
        stmt = stmt.where(OntologyEdge.relation_type.in_(relation_types))
    return list(db.execute(stmt.limit(limit)).scalars().all())
