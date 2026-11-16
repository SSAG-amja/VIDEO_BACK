from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.crud.recsys.ontology import (
    create_build,
    get_build_by_source_hash,
    mark_build_failed,
    mark_build_success,
)
from app.db.session import SessionLocal
from app.jobs.recsys.v3.ontology.ontology_asset_validator import (
    validate_assets,
    validate_db_source_coverage,
)
from app.jobs.recsys.v3.ontology.ontology_graph_builder import ASSET_DIR, V3OntologyGraphBuilder
from app.services.recsys.v3.config import (
    ENGINE_VERSION,
    ONTOLOGY_AGGREGATION_POLICY_VERSION,
)
from app.services.recsys.v3.domain.ontology_registry import (
    ONTOLOGY_ENGINE_NAME,
    ONTOLOGY_SCHEMA_VERSION,
    RELATION_REGISTRY_VERSION,
)


SOURCE_COLUMNS = {
    "movies": ("id", "tmdb_id", "imdb_id", "title", "title_ko", "adult"),
    "genres": ("id", "tmdb_id", "name", "name_ko"),
    "keywords": ("id", "tmdb_id", "name"),
    "people": ("id", "tmdb_id", "name", "name_ko"),
    "otts": ("id", "tmdb_id", "name", "name_ko"),
    "movie_genres": ("movie_id", "genre_id"),
    "movie_keywords": ("movie_id", "keyword_id"),
    "movie_actors": ("movie_id", "actor_id", "cast_name"),
    "movie_directors": ("movie_id", "director_id"),
    "movie_otts": ("movie_id", "ott_id", "is_streaming", "is_rent", "is_buy"),
    "movie_overview_semantic_signals": (
        "movie_id",
        "signal_type",
        "signal_key",
        "weight",
        "confidence",
        "matched_terms",
        "overview_hash",
        "asset_version",
        "extractor_version",
    ),
}


def compute_manifest_hash(manifest: dict[str, Any]) -> str:
    serialized = json.dumps(manifest, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def build_asset_manifest(asset_dir: Path | str = ASSET_DIR) -> dict[str, Any]:
    base_dir = Path(asset_dir)
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(base_dir.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
        files[path.name] = {
            "version": payload.get("version"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return files


def build_db_source_fingerprint(db: Session) -> dict[str, dict[str, int | str]]:
    fingerprint: dict[str, dict[str, int | str]] = {}
    for table_name, columns in SOURCE_COLUMNS.items():
        selected_columns = ", ".join(columns)
        row = db.execute(
            text(
                f"""
                SELECT count(*) AS row_count,
                       COALESCE(
                           bit_xor(hashtextextended(to_jsonb(source_row)::text, 0)),
                           0
                       )::text AS content_xor
                FROM (
                    SELECT {selected_columns}
                    FROM {table_name}
                ) source_row
                """
            )
        ).one()
        fingerprint[table_name] = {
            "row_count": int(row.row_count),
            "content_xor": str(row.content_xor),
        }
    return fingerprint


def build_source_manifest(
    db: Session,
    *,
    asset_dir: Path | str = ASSET_DIR,
) -> dict[str, Any]:
    asset_validation = validate_assets(asset_dir)
    asset_source_coverage = validate_db_source_coverage(db, asset_dir=asset_dir)
    return {
        "engine_version": ENGINE_VERSION,
        "ontology_engine_name": ONTOLOGY_ENGINE_NAME,
        "ontology_schema_version": ONTOLOGY_SCHEMA_VERSION,
        "relation_registry_version": RELATION_REGISTRY_VERSION,
        "aggregation_policy_version": ONTOLOGY_AGGREGATION_POLICY_VERSION,
        "required_relations": {
            "actor": True,
            "director": True,
            "overview_semantics": True,
            "ott_modes": ("streaming", "rent", "buy"),
        },
        "assets": build_asset_manifest(asset_dir),
        "asset_validation": asdict(asset_validation),
        "asset_source_coverage": asset_source_coverage,
        "db_source_fingerprint": build_db_source_fingerprint(db),
    }


def run_graph_build_pipeline(
    db: Session,
    *,
    asset_dir: Path | str = ASSET_DIR,
) -> int:
    source_manifest = build_source_manifest(db, asset_dir=asset_dir)
    source_hash = compute_manifest_hash(source_manifest)
    build = get_build_by_source_hash(
        db,
        source_hash,
        engine_name=ONTOLOGY_ENGINE_NAME,
        schema_version=ONTOLOGY_SCHEMA_VERSION,
    )
    if build is not None and build.status == "success":
        return build.id

    if build is None:
        build = create_build(
            db,
            engine_name=ONTOLOGY_ENGINE_NAME,
            schema_version=ONTOLOGY_SCHEMA_VERSION,
            version=ENGINE_VERSION,
            source_hash=source_hash,
            source_manifest=source_manifest,
            properties={
                "activation_blocked_until": "feature_export_validation",
                "attempt_count": 1,
            },
        )
    else:
        attempt_count = int((build.properties or {}).get("attempt_count", 0)) + 1
        build.status = "running"
        build.is_active = False
        build.started_at = func.now()
        build.finished_at = None
        build.node_count = 0
        build.edge_count = 0
        build.evidence_count = 0
        build.error_message = None
        build.source_manifest = source_manifest
        build.properties = {
            **(build.properties or {}),
            "activation_blocked_until": "feature_export_validation",
            "attempt_count": attempt_count,
            "stage_metrics": [],
        }
    db.commit()

    try:
        builder = V3OntologyGraphBuilder(db, asset_dir=asset_dir)
        node_count, edge_count, evidence_count = builder.build(build)
        ending_fingerprint = build_db_source_fingerprint(db)
        if ending_fingerprint != source_manifest["db_source_fingerprint"]:
            raise RuntimeError("ontology source tables changed during V3 graph build")
        build.source_manifest = {
            **source_manifest,
            "ending_db_source_fingerprint": ending_fingerprint,
        }
        mark_build_success(
            db,
            build,
            node_count=node_count,
            edge_count=edge_count,
            evidence_count=evidence_count,
            activate=False,
        )
        db.commit()
        return build.id
    except Exception as exc:
        db.rollback()
        failed_build = get_build_by_source_hash(
            db,
            source_hash,
            engine_name=ONTOLOGY_ENGINE_NAME,
            schema_version=ONTOLOGY_SCHEMA_VERSION,
        )
        if failed_build is not None:
            failed_build.is_active = False
            mark_build_failed(db, failed_build, error_message=str(exc))
            db.commit()
        raise


def main() -> None:
    with SessionLocal() as db:
        build_id = run_graph_build_pipeline(db)
    print(f"V3 ontology graph build completed build_id={build_id}; activation pending feature export")


if __name__ == "__main__":
    main()
