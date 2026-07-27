from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud.recsys.ontology import get_active_build
from app.db.session import SessionLocal
from app.jobs.recsys.v2.overview_signal_extractor import EXTRACTOR_VERSION


DEFAULT_BATCH_SIZE = 100_000


def get_signal_movie_id_range(db: Session, *, extractor_version: str = EXTRACTOR_VERSION) -> tuple[int, int] | None:
    row = db.execute(
        text(
            """
            SELECT min(movie_id) AS min_id, max(movie_id) AS max_id
            FROM movie_overview_semantic_signals
            WHERE extractor_version = :extractor_version
            """
        ),
        {"extractor_version": extractor_version},
    ).one()
    if row.min_id is None or row.max_id is None:
        return None
    return int(row.min_id), int(row.max_id)


def batched_ranges(min_id: int, max_id: int, batch_size: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = min_id
    while start <= max_id:
        end = min(start + batch_size, max_id + 1)
        ranges.append((start, end))
        start = end
    return ranges


def count_build_rows(db: Session, *, build_id: int, table_name: str) -> int:
    return int(
        db.execute(
            text(f"SELECT count(*) FROM {table_name} WHERE build_id = :build_id"),
            {"build_id": build_id},
        ).scalar_one()
    )


def materialize_overview_edges_for_build(
    db: Session,
    *,
    build_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    extractor_version: str = EXTRACTOR_VERSION,
) -> int:
    id_range = get_signal_movie_id_range(db, extractor_version=extractor_version)
    if id_range is None:
        return 0

    total_signals = 0
    for start_id, end_id in batched_ranges(id_range[0], id_range[1], batch_size):
        result = db.execute(
            text(
                """
                INSERT INTO ontology_edges (
                    build_id, source_node_id, target_node_id, relation_type, weight,
                    confidence, source, properties, created_at
                )
                SELECT
                    :build_id,
                    movie_node.id,
                    target_node.id,
                    CASE
                        WHEN signal.signal_type = 'theme' THEN 'has_theme'
                        WHEN signal.signal_type = 'mood' THEN 'has_mood'
                    END,
                    signal.weight,
                    signal.confidence,
                    'overview_signal',
                    json_build_object(
                        'sources', json_build_array('movies.overview', 'movie_overview_semantic_signals'),
                        'signal_key', signal.signal_key,
                        'matched_terms', signal.matched_terms,
                        'asset_version', signal.asset_version,
                        'extractor_version', signal.extractor_version
                    ),
                    now()
                FROM movie_overview_semantic_signals signal
                JOIN ontology_nodes movie_node
                    ON movie_node.build_id = :build_id
                    AND movie_node.node_type = 'movie'
                    AND movie_node.ref_id = signal.movie_id::text
                JOIN ontology_nodes target_node
                    ON target_node.build_id = :build_id
                    AND target_node.node_type = signal.signal_type
                    AND target_node.ref_id = signal.signal_key
                WHERE signal.extractor_version = :extractor_version
                  AND signal.movie_id >= :start_id
                  AND signal.movie_id < :end_id
                  AND signal.signal_type IN ('theme', 'mood')
                ON CONFLICT (build_id, source_node_id, target_node_id, relation_type)
                DO UPDATE SET
                    weight = GREATEST(ontology_edges.weight, EXCLUDED.weight),
                    confidence = GREATEST(ontology_edges.confidence, EXCLUDED.confidence),
                    source = CASE
                        WHEN ontology_edges.source = EXCLUDED.source THEN ontology_edges.source
                        WHEN ontology_edges.source LIKE '%overview_signal%' THEN ontology_edges.source
                        ELSE left(ontology_edges.source || '+overview_signal', 50)
                    END,
                    properties = EXCLUDED.properties
                """
            ),
            {
                "build_id": build_id,
                "extractor_version": extractor_version,
                "start_id": start_id,
                "end_id": end_id,
            },
        )
        total_signals += result.rowcount if result.rowcount and result.rowcount > 0 else 0
        db.commit()
        print(
            f"overview materialize: build_id={build_id}, range={start_id}-{end_id - 1}, affected={total_signals}",
            flush=True,
        )
    return total_signals


def refresh_build_counts(db: Session, *, build_id: int, affected_signal_count: int) -> None:
    node_count = count_build_rows(db, build_id=build_id, table_name="ontology_nodes")
    edge_count = count_build_rows(db, build_id=build_id, table_name="ontology_edges")
    db.execute(
        text(
            """
            UPDATE ontology_builds
            SET
                node_count = :node_count,
                edge_count = :edge_count,
                properties = COALESCE(properties, '{}'::json)::jsonb
                    || jsonb_build_object(
                        'overview_materialization',
                        jsonb_build_object(
                            'extractor_version', :extractor_version,
                            'affected_signal_count', :affected_signal_count,
                            'materialized_at', now()
                        )
                    )
            WHERE id = :build_id
            """
        ),
        {
            "build_id": build_id,
            "node_count": node_count,
            "edge_count": edge_count,
            "extractor_version": EXTRACTOR_VERSION,
            "affected_signal_count": affected_signal_count,
        },
    )
    db.commit()


def run_overview_edge_materialization(db: Session, *, build_id: int | None = None) -> int:
    build = None
    if build_id is None:
        build = get_active_build(db)
        if build is None:
            raise RuntimeError("no active ontology build found")
        build_id = build.id

    affected_signal_count = materialize_overview_edges_for_build(db, build_id=build_id)
    refresh_build_counts(db, build_id=build_id, affected_signal_count=affected_signal_count)
    return build_id


def run_worker() -> int:
    db = SessionLocal()
    try:
        return run_overview_edge_materialization(db)
    finally:
        db.close()


if __name__ == "__main__":
    materialized_build_id = run_worker()
    print(f"overview edge materialization completed build_id={materialized_build_id}", flush=True)
