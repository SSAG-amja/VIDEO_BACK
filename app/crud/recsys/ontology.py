from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.ontology import OntologyBuild, OntologyEdge, OntologyNode


def get_active_build(db: Session) -> OntologyBuild | None:
    stmt = (
        select(OntologyBuild)
        .where(OntologyBuild.is_active.is_(True), OntologyBuild.status == "success")
        .order_by(OntologyBuild.started_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_build_by_source_hash(db: Session, source_hash: str) -> OntologyBuild | None:
    stmt = select(OntologyBuild).where(OntologyBuild.source_hash == source_hash).limit(1)
    return db.execute(stmt).scalar_one_or_none()


def create_build(
    db: Session,
    *,
    version: str,
    source_hash: str,
    properties: dict | None = None,
) -> OntologyBuild:
    build = OntologyBuild(
        version=version,
        status="running",
        is_active=False,
        source_hash=source_hash,
        properties=properties,
    )
    db.add(build)
    db.flush()
    return build


def mark_build_success(db: Session, build: OntologyBuild, *, node_count: int, edge_count: int) -> OntologyBuild:
    db.execute(update(OntologyBuild).where(OntologyBuild.is_active.is_(True)).values(is_active=False, status="inactive"))
    build.status = "success"
    build.is_active = True
    build.node_count = node_count
    build.edge_count = edge_count
    build.finished_at = func.now()
    build.error_message = None
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
