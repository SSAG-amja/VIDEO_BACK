import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.crud.recsys.ontology import get_build_by_source_hash, mark_build_failed, mark_build_success
from app.services.recsys.v2.config import ENGINE_VERSION
from app.services.recsys.v2.graph_builder import build_ontology_graph, start_graph_build
from app.jobs.recsys.v2.validate_assets import ASSET_DIR, validate_assets


def compute_source_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def build_asset_source_payload(asset_dir: Path | str = ASSET_DIR) -> dict:
    base_dir = Path(asset_dir)
    files: dict[str, dict] = {}
    for path in sorted(base_dir.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
        files[path.name] = {
            "version": payload.get("version"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "engine_version": ENGINE_VERSION,
        "assets": files,
    }


def run_graph_build_pipeline(
    db: Session,
    *,
    source_payload: dict | None = None,
    include_actor_nodes: bool | None = None,
    include_actor_edges: bool | None = None,
    include_overview_derivation: bool | None = None,
) -> int:
    validation_errors = validate_assets()
    if validation_errors:
        raise ValueError("ontology asset validation failed:\n" + "\n".join(validation_errors))

    payload = source_payload or build_asset_source_payload()
    build_options = {
        key: value
        for key, value in {
            "include_actor_nodes": include_actor_nodes,
            "include_actor_edges": include_actor_edges,
            "include_overview_derivation": include_overview_derivation,
        }.items()
        if value is not None
    }
    if build_options:
        payload = {**payload, "build_options": build_options}
    source_hash = compute_source_hash(payload)
    existing_build = get_build_by_source_hash(db, source_hash)
    if existing_build and existing_build.status == "success":
        return existing_build.id

    if existing_build:
        build = existing_build
        build.status = "running"
        build.is_active = False
        build.error_message = None
        build.properties = {"source_payload": payload}
        db.flush()
    else:
        build = start_graph_build(
            db,
            version=ENGINE_VERSION,
            source_hash=source_hash,
            properties={"source_payload": payload},
        )

    try:
        build_kwargs = {
            key: value
            for key, value in {
                "include_actor_nodes": include_actor_nodes,
                "include_actor_edges": include_actor_edges,
                "include_overview_derivation": include_overview_derivation,
            }.items()
            if value is not None
        }
        node_count, edge_count = build_ontology_graph(db, build=build, **build_kwargs)
        mark_build_success(db, build, node_count=node_count, edge_count=edge_count)
        db.commit()
        return build.id
    except Exception as exc:
        db.rollback()
        build = get_build_by_source_hash(db, source_hash)
        if build is None:
            build = start_graph_build(
                db,
                version=ENGINE_VERSION,
                source_hash=source_hash,
                properties={"source_payload": payload},
            )
        mark_build_failed(db, build, error_message=str(exc))
        db.commit()
        raise
