import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud.recsys.ontology import create_build
from app.jobs.recsys.v2.overview_signal_extractor import EXTRACTOR_VERSION
from app.jobs.recsys.v2.validate_assets import ASSET_DIR
from app.models.ontology import OntologyBuild
from app.services.recsys.v2.config import (
    ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD,
    ENABLE_ACTOR_NODES_IN_GRAPH_BUILD,
    ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD,
    GRAPH_BUILD_BATCH_SIZE,
)


DIRECT_RELATION_WEIGHTS = {
    "has_genre": 1.0,
    "has_keyword": 0.7,
    "has_actor": 0.5,
    "has_director": 0.9,
    "available_on": 0.0,
}


def start_graph_build(db: Session, *, version: str, source_hash: str, properties: dict | None = None) -> OntologyBuild:
    return create_build(db, version=version, source_hash=source_hash, properties=properties)


def build_ontology_graph(
    db: Session,
    *,
    build: OntologyBuild,
    asset_dir: Path | str = ASSET_DIR,
    batch_size: int = GRAPH_BUILD_BATCH_SIZE,
    include_actor_nodes: bool = ENABLE_ACTOR_NODES_IN_GRAPH_BUILD,
    include_actor_edges: bool = ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD,
    include_overview_derivation: bool = ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD,
) -> tuple[int, int]:
    assets = load_ontology_assets(asset_dir)
    reset_build_rows(db, build_id=build.id)
    db.commit()

    print("ontology build: creating db nodes")
    create_db_nodes(db, build_id=build.id, batch_size=batch_size, include_actor_nodes=include_actor_nodes)
    db.commit()

    print("ontology build: creating manual nodes")
    create_manual_nodes(db, build_id=build.id, assets=assets)
    db.commit()

    print("ontology build: creating direct edges")
    create_direct_edges(db, build_id=build.id, batch_size=batch_size, include_actor_edges=include_actor_edges)
    db.commit()

    print("ontology build: creating asset edges")
    create_asset_edges(db, build_id=build.id, assets=assets)
    db.commit()

    print("ontology build: creating movie semantic edges from asset rules")
    create_movie_semantic_edges_from_asset_rules(db, build_id=build.id, batch_size=batch_size)
    db.commit()

    if include_overview_derivation:
        print("ontology build: creating movie semantic edges from overview")
        create_movie_semantic_edges_from_overview(db, build_id=build.id, assets=assets, batch_size=batch_size)
        db.commit()

    node_count = count_rows(db, "ontology_nodes", build.id)
    edge_count = count_rows(db, "ontology_edges", build.id)
    return node_count, edge_count


def reset_build_rows(db: Session, *, build_id: int) -> None:
    db.execute(text("DELETE FROM ontology_edges WHERE build_id = :build_id"), {"build_id": build_id})
    db.execute(text("DELETE FROM ontology_nodes WHERE build_id = :build_id"), {"build_id": build_id})
    db.flush()


def load_ontology_assets(asset_dir: Path | str = ASSET_DIR) -> dict[str, Any]:
    base_dir = Path(asset_dir)
    loaded: dict[str, Any] = {}
    for path in sorted(base_dir.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            loaded[path.name] = json.load(file)
    return loaded


def count_rows(db: Session, table_name: str, build_id: int) -> int:
    return int(
        db.execute(
            text(f"SELECT count(*) FROM {table_name} WHERE build_id = :build_id"),
            {"build_id": build_id},
        ).scalar_one()
    )


def get_id_range(db: Session, table_name: str, id_column: str = "id") -> tuple[int, int] | None:
    row = db.execute(
        text(f"SELECT min({id_column}) AS min_id, max({id_column}) AS max_id FROM {table_name}")
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


def execute_batched_movie_statement(
    db: Session,
    *,
    statement: str,
    build_id: int,
    table_name: str,
    batch_size: int,
    extra_params: dict[str, Any] | None = None,
) -> None:
    id_range = get_id_range(db, table_name, "id")
    if id_range is None:
        return
    min_id, max_id = id_range
    params = extra_params or {}
    for start_id, end_id in batched_ranges(min_id, max_id, batch_size):
        db.execute(
            text(statement),
            {
                "build_id": build_id,
                "start_id": start_id,
                "end_id": end_id,
                **params,
            },
        )
        db.commit()


def execute_batched_mapping_statement(
    db: Session,
    *,
    statement: str,
    build_id: int,
    table_name: str,
    batch_size: int,
    extra_params: dict[str, Any] | None = None,
) -> None:
    row = db.execute(text(f"SELECT min(movie_id) AS min_id, max(movie_id) AS max_id FROM {table_name}")).one()
    if row.min_id is None or row.max_id is None:
        return
    params = extra_params or {}
    for start_id, end_id in batched_ranges(int(row.min_id), int(row.max_id), batch_size):
        db.execute(
            text(statement),
            {
                "build_id": build_id,
                "start_id": start_id,
                "end_id": end_id,
                **params,
            },
        )
        db.commit()


def create_db_nodes(
    db: Session,
    *,
    build_id: int,
    batch_size: int = GRAPH_BUILD_BATCH_SIZE,
    include_actor_nodes: bool = ENABLE_ACTOR_NODES_IN_GRAPH_BUILD,
) -> None:
    movie_statement = """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'movie',
            m.id::text,
            COALESCE(NULLIF(m.title_ko, ''), NULLIF(m.title, ''), m.id::text),
            m.title_ko,
            m.title,
            'db',
            'movies',
            1.0,
            true,
            json_build_object(
                'tmdb_id', m.tmdb_id,
                'imdb_id', m.imdb_id,
                'original_language', m.original_language,
                'has_overview', m.overview IS NOT NULL AND length(trim(m.overview)) > 0
            ),
            now(),
            now()
        FROM movies m
        WHERE m.id >= :start_id AND m.id < :end_id
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """
    execute_batched_movie_statement(
        db,
        statement=movie_statement,
        build_id=build_id,
        table_name="movies",
        batch_size=batch_size,
    )

    statements = [
        """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'genre',
            g.id::text,
            COALESCE(NULLIF(g.name_ko, ''), g.name),
            g.name_ko,
            g.name,
            'db',
            'genres',
            1.0,
            true,
            json_build_object('tmdb_id', g.tmdb_id),
            now(),
            now()
        FROM genres g
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """,
        """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'keyword',
            k.id::text,
            k.name,
            NULL,
            k.name,
            'db',
            'keywords',
            1.0,
            true,
            json_build_object('tmdb_id', k.tmdb_id),
            now(),
            now()
        FROM keywords k
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """,
    ]
    if include_actor_nodes:
        statements.append(
        """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'actor',
            p.id::text,
            COALESCE(NULLIF(p.name_ko, ''), p.name),
            p.name_ko,
            p.name,
            'db',
            'people',
            1.0,
            true,
            json_build_object('tmdb_id', p.tmdb_id),
            now(),
            now()
        FROM people p
        WHERE EXISTS (SELECT 1 FROM movie_actors ma WHERE ma.actor_id = p.id)
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """
        )
    statements.extend([
        """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'director',
            p.id::text,
            COALESCE(NULLIF(p.name_ko, ''), p.name),
            p.name_ko,
            p.name,
            'db',
            'people',
            1.0,
            true,
            json_build_object('tmdb_id', p.tmdb_id),
            now(),
            now()
        FROM people p
        WHERE EXISTS (SELECT 1 FROM movie_directors md WHERE md.director_id = p.id)
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """,
        """
        INSERT INTO ontology_nodes (
            build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
            confidence, is_active, properties, created_at, updated_at
        )
        SELECT
            :build_id,
            'ott',
            o.id::text,
            COALESCE(NULLIF(o.name_ko, ''), o.name),
            o.name_ko,
            o.name,
            'db',
            'otts',
            1.0,
            true,
            json_build_object('tmdb_id', o.tmdb_id),
            now(),
            now()
        FROM otts o
        ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
        """,
    ])
    for statement in statements:
        db.execute(text(statement), {"build_id": build_id})
        db.commit()
    db.flush()


def create_manual_nodes(db: Session, *, build_id: int, assets: dict[str, Any]) -> None:
    for node_type, filename in (("theme", "themes.json"), ("mood", "moods.json")):
        items = assets.get(filename, {}).get("items", [])
        for item in items:
            db.execute(
                text(
                    """
                    INSERT INTO ontology_nodes (
                        build_id, node_type, ref_id, label, label_ko, label_en, source, source_table,
                        confidence, is_active, properties, created_at, updated_at
                    )
                    VALUES (
                        :build_id, :node_type, :ref_id, :label, :label_ko, :label_en, 'manual_asset', NULL,
                        1.0, true, CAST(:properties AS JSON), now(), now()
                    )
                    ON CONFLICT (build_id, node_type, ref_id) DO NOTHING
                    """
                ),
                {
                    "build_id": build_id,
                    "node_type": node_type,
                    "ref_id": item["key"],
                    "label": item["label_ko"] or item["label_en"],
                    "label_ko": item["label_ko"],
                    "label_en": item["label_en"],
                    "properties": json.dumps(
                        {
                            "aliases": item.get("aliases", []),
                            "description": item.get("description"),
                            "asset_version": item.get("version"),
                        }
                    ),
                },
            )
    db.flush()


def create_direct_edges(
    db: Session,
    *,
    build_id: int,
    batch_size: int = GRAPH_BUILD_BATCH_SIZE,
    include_actor_edges: bool = ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD,
) -> None:
    direct_edges = [
        ("movie_genres", "genre_id", "genre", "has_genre", DIRECT_RELATION_WEIGHTS["has_genre"]),
        ("movie_keywords", "keyword_id", "keyword", "has_keyword", DIRECT_RELATION_WEIGHTS["has_keyword"]),
        ("movie_directors", "director_id", "director", "has_director", DIRECT_RELATION_WEIGHTS["has_director"]),
        ("movie_otts", "ott_id", "ott", "available_on", DIRECT_RELATION_WEIGHTS["available_on"]),
    ]
    if include_actor_edges:
        direct_edges.append(("movie_actors", "actor_id", "actor", "has_actor", DIRECT_RELATION_WEIGHTS["has_actor"]))
    for table_name, target_column, target_type, relation_type, weight in direct_edges:
        print(f"ontology build: creating {relation_type} edges")
        execute_batched_mapping_statement(
            db,
            statement=f"""
            INSERT INTO ontology_edges (
                build_id, source_node_id, target_node_id, relation_type, weight,
                confidence, source, properties, created_at
            )
            SELECT
                :build_id,
                movie_node.id,
                target_node.id,
                :relation_type,
                :weight,
                1.0,
                'db',
                json_build_object('sources', json_build_array(:table_name)),
                now()
            FROM {table_name} mapping
            JOIN ontology_nodes movie_node
                ON movie_node.build_id = :build_id
                AND movie_node.node_type = 'movie'
                AND movie_node.ref_id = mapping.movie_id::text
            JOIN ontology_nodes target_node
                ON target_node.build_id = :build_id
                AND target_node.node_type = :target_type
                AND target_node.ref_id = mapping.{target_column}::text
            WHERE mapping.movie_id >= :start_id AND mapping.movie_id < :end_id
            ON CONFLICT (build_id, source_node_id, target_node_id, relation_type) DO NOTHING
            """,
            build_id=build_id,
            table_name=table_name,
            batch_size=batch_size,
            extra_params={
                "relation_type": relation_type,
                "weight": weight,
                "table_name": table_name,
                "target_type": target_type,
            },
        )
    db.flush()


def create_asset_edges(db: Session, *, build_id: int, assets: dict[str, Any]) -> None:
    for filename in (
        "genre_theme_mood_rules.json",
        "keyword_theme_mood_rules.json",
        "theme_relations.json",
        "mood_relations.json",
    ):
        for relation in assets.get(filename, {}).get("relations", []):
            source_node_id = resolve_asset_relation_source_node_id(db, build_id=build_id, relation=relation)
            target_node_id = resolve_asset_relation_target_node_id(db, build_id=build_id, relation=relation)
            if source_node_id is None or target_node_id is None:
                continue
            db.execute(
                text(
                    """
                    INSERT INTO ontology_edges (
                        build_id, source_node_id, target_node_id, relation_type, weight,
                        confidence, source, properties, created_at
                    )
                    VALUES (
                        :build_id, :source_node_id, :target_node_id, :relation_type, :weight,
                        :confidence, 'manual_asset', CAST(:properties AS JSON), now()
                    )
                    ON CONFLICT (build_id, source_node_id, target_node_id, relation_type) DO NOTHING
                    """
                ),
                {
                    "build_id": build_id,
                    "source_node_id": source_node_id,
                    "target_node_id": target_node_id,
                    "relation_type": relation["relation_type"],
                    "weight": relation["weight"],
                    "confidence": relation["confidence"],
                    "properties": json.dumps(
                        {
                            "sources": [filename],
                            "description": relation.get("description"),
                            "asset_version": relation.get("version"),
                            "source_key": relation.get("source_key"),
                            "target_key": relation.get("target_key"),
                        }
                    ),
                },
            )
    db.flush()


def resolve_asset_relation_source_node_id(db: Session, *, build_id: int, relation: dict[str, Any]) -> int | None:
    return resolve_node_id(db, build_id=build_id, node_type=relation["source_type"], key=relation["source_key"])


def resolve_asset_relation_target_node_id(db: Session, *, build_id: int, relation: dict[str, Any]) -> int | None:
    return resolve_node_id(db, build_id=build_id, node_type=relation["target_type"], key=relation["target_key"])


def resolve_node_id(db: Session, *, build_id: int, node_type: str, key: str) -> int | None:
    if node_type in {"theme", "mood"}:
        return db.execute(
            text(
                """
                SELECT id
                FROM ontology_nodes
                WHERE build_id = :build_id AND node_type = :node_type AND ref_id = :key
                LIMIT 1
                """
            ),
            {"build_id": build_id, "node_type": node_type, "key": key},
        ).scalar_one_or_none()

    return db.execute(
        text(
            """
            SELECT id
            FROM ontology_nodes
            WHERE build_id = :build_id
              AND node_type = :node_type
              AND (lower(label) = lower(:key) OR lower(coalesce(label_en, '')) = lower(:key) OR lower(coalesce(label_ko, '')) = lower(:key))
            LIMIT 1
            """
        ),
        {"build_id": build_id, "node_type": node_type, "key": key},
    ).scalar_one_or_none()


def create_movie_semantic_edges_from_asset_rules(
    db: Session,
    *,
    build_id: int,
    batch_size: int = GRAPH_BUILD_BATCH_SIZE,
) -> None:
    # movie -> keyword/genre -> theme/mood rules become movie -> theme/mood edges.
    for source_relation_type in ("has_keyword", "has_genre"):
        print(f"ontology build: deriving movie semantic edges from {source_relation_type}")
        id_range = get_movie_node_id_range(db, build_id=build_id)
        if id_range is None:
            continue
        for start_id, end_id in batched_ranges(id_range[0], id_range[1], batch_size):
            db.execute(
                text(
                    """
                    INSERT INTO ontology_edges (
                        build_id, source_node_id, target_node_id, relation_type, weight,
                        confidence, source, properties, created_at
                    )
                    SELECT
                        :build_id,
                        movie_edge.source_node_id,
                        semantic_edge.target_node_id,
                        CASE
                            WHEN semantic_edge.relation_type = 'implies_theme' THEN 'has_theme'
                            WHEN semantic_edge.relation_type = 'implies_mood' THEN 'has_mood'
                        END,
                        movie_edge.weight * semantic_edge.weight,
                        movie_edge.confidence * semantic_edge.confidence,
                        'derived',
                        json_build_object(
                            'sources', json_build_array('asset_rule_propagation'),
                            'via_relation', semantic_edge.relation_type,
                            'via_node_id', movie_edge.target_node_id
                        ),
                        now()
                    FROM ontology_edges movie_edge
                    JOIN ontology_edges semantic_edge
                        ON semantic_edge.build_id = :build_id
                        AND semantic_edge.source_node_id = movie_edge.target_node_id
                        AND semantic_edge.relation_type IN ('implies_theme', 'implies_mood')
                    WHERE movie_edge.build_id = :build_id
                      AND movie_edge.relation_type = :source_relation_type
                      AND movie_edge.source_node_id >= :start_id
                      AND movie_edge.source_node_id < :end_id
                    ON CONFLICT (build_id, source_node_id, target_node_id, relation_type) DO NOTHING
                    """
                ),
                {
                    "build_id": build_id,
                    "source_relation_type": source_relation_type,
                    "start_id": start_id,
                    "end_id": end_id,
                },
            )
            db.commit()
    db.flush()


def get_movie_node_id_range(db: Session, *, build_id: int) -> tuple[int, int] | None:
    row = db.execute(
        text(
            """
            SELECT min(id) AS min_id, max(id) AS max_id
            FROM ontology_nodes
            WHERE build_id = :build_id AND node_type = 'movie'
            """
        ),
        {"build_id": build_id},
    ).one()
    if row.min_id is None or row.max_id is None:
        return None
    return int(row.min_id), int(row.max_id)


def create_movie_semantic_edges_from_overview(
    db: Session,
    *,
    build_id: int,
    assets: dict[str, Any],
    batch_size: int = GRAPH_BUILD_BATCH_SIZE,
) -> None:
    row = db.execute(
        text(
            """
            SELECT min(movie_id) AS min_id, max(movie_id) AS max_id
            FROM movie_overview_semantic_signals
            WHERE extractor_version = :extractor_version
            """
        ),
        {"extractor_version": EXTRACTOR_VERSION},
    ).one()
    if row.min_id is None or row.max_id is None:
        return

    for start_id, end_id in batched_ranges(int(row.min_id), int(row.max_id), batch_size):
        db.execute(
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
                    properties = EXCLUDED.properties
                """
            ),
            {
                "build_id": build_id,
                "extractor_version": EXTRACTOR_VERSION,
                "start_id": start_id,
                "end_id": end_id,
            },
        )
        db.commit()
    db.flush()


def overview_patterns_for_item(item: dict[str, Any]) -> list[str]:
    raw_terms = [
        item.get("key"),
        item.get("label_ko"),
        item.get("label_en"),
        *item.get("aliases", []),
    ]
    patterns: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if not isinstance(term, str):
            continue
        normalized = term.strip()
        if len(normalized) < 4:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        patterns.append(f"%{normalized}%")
    return patterns
