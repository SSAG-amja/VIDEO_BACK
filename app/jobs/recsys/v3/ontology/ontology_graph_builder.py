from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.jobs.recsys.v3.ontology.ontology_asset_validator import (
    load_assets,
    validate_assets,
)
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.config import (
    ONTOLOGY_AGGREGATION_POLICY_VERSION,
    ONTOLOGY_BUILD_BATCH_SIZE,
    ONTOLOGY_BUILD_WORKER_COUNT,
    ONTOLOGY_BUILD_WORK_MEM,
)
from app.services.recsys.v3.domain.ontology_registry import (
    ONTOLOGY_ENGINE_NAME,
    ONTOLOGY_SCHEMA_VERSION,
    NodeType,
    get_relation_definition,
    validate_relation_registry,
)


ASSET_DIR = Path(__file__).resolve().parents[5] / "assets" / "ontology" / "v3"
LEGACY_RELATION_NAMES = {
    "implies_theme": "suggests_theme",
    "implies_mood": "suggests_mood",
}

INCOMPLETE_BUILD_TABLES = (
    ("ontology_edge_evidence", "reset_edge_evidence"),
    ("ontology_edges", "reset_edges"),
    ("ontology_nodes", "reset_nodes"),
)
SEMANTIC_RELATIONS = {"has_theme", "has_mood"}


@dataclass(frozen=True)
class CatalogChunk:
    start_ordinal: int
    end_ordinal: int
    start_movie_id: int
    end_movie_id: int


@dataclass(frozen=True)
class ParallelWorkerMetric:
    worker_id: int
    chunks: int
    rows: int
    active_seconds: float
    elapsed_seconds: float


def aggregate_evidence_strength(evidence: list[tuple[str, float]]) -> float:
    """Combine the strongest evidence per source family with a bounded union."""
    family_strengths: dict[str, float] = {}
    for family, strength in evidence:
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"evidence strength must be in [0, 1] strength={strength}")
        family_strengths[family] = max(strength, family_strengths.get(family, 0.0))

    remaining = 1.0
    for strength in family_strengths.values():
        remaining *= 1.0 - strength
    return 1.0 - remaining


class V3OntologyGraphBuilder:
    """Materialize an immutable V3 graph without mutating V2 build rows."""

    def __init__(
        self,
        db: Session,
        *,
        asset_dir: Path | str = ASSET_DIR,
        batch_size: int = ONTOLOGY_BUILD_BATCH_SIZE,
        worker_count: int = ONTOLOGY_BUILD_WORKER_COUNT,
        strict_asset_resolution: bool = True,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("ontology build batch_size must be positive")
        if worker_count <= 0:
            raise ValueError("ontology build worker_count must be positive")
        self.db = db
        self.asset_dir = Path(asset_dir)
        self.batch_size = batch_size
        self.worker_count = worker_count
        self.strict_asset_resolution = strict_asset_resolution
        self.session_factory = session_factory
        self.stage_metrics: list[dict[str, Any]] = []
        self._catalog_table: str | None = None
        self._evidence_stage_table: str | None = None
        self._catalog_chunks: tuple[CatalogChunk, ...] = ()
        self._stage_details: dict[str, Any] = {}

    def build(self, build: OntologyBuild) -> tuple[int, int, int]:
        self._validate_build(build)
        validate_relation_registry()
        assets = self._load_assets()

        self._catalog_table = f"public.v3_ontology_catalog_{build.id}"
        self._evidence_stage_table = f"public.v3_ontology_evidence_{build.id}"
        try:
            return self._materialize_graph(build, assets)
        except Exception:
            self.db.rollback()
            try:
                self._drop_staging_tables()
                self.db.commit()
            except Exception:
                self.db.rollback()
            raise

    def _materialize_graph(
        self,
        build: OntologyBuild,
        assets: dict[str, Any],
    ) -> tuple[int, int, int]:
        self._run_stage(build, "stale_staging_cleanup", self._drop_staging_tables)
        for table_name, stage_name in INCOMPLETE_BUILD_TABLES:
            self._run_stage(
                build,
                stage_name,
                lambda table_name=table_name: self._delete_build_rows(table_name, build.id),
            )
        self._run_stage(build, "ontology_catalog", lambda: self._create_catalog(build.id))
        for node_stage in (
            "movie_nodes",
            "genre_nodes",
            "keyword_nodes",
            "person_nodes",
            "ott_nodes",
        ):
            self._run_stage(
                build,
                node_stage,
                lambda node_stage=node_stage: self._create_factual_nodes(
                    build.id,
                    node_stages={node_stage},
                ),
            )
        self._run_stage(build, "concept_nodes", lambda: self._create_concept_nodes(build.id, assets))
        self._run_stage(
            build,
            "node_statistics",
            lambda: self._analyze_tables(("ontology_nodes",)),
        )
        for relation_type in (
            "has_genre",
            "has_keyword",
            "has_actor",
            "has_director",
            "available_streaming_on",
            "available_rent_on",
            "available_buy_on",
        ):
            self._run_stage(
                build,
                relation_type,
                lambda relation_type=relation_type: self._create_factual_edges(
                    build.id,
                    relation_types={relation_type},
                ),
            )
        self._run_stage(build, "asset_edges", lambda: self._create_asset_edges(build.id, assets))
        self._run_stage(
            build,
            "factual_edge_statistics",
            lambda: self._analyze_tables(("ontology_edges",)),
        )
        self._run_stage(build, "semantic_evidence", lambda: self._create_semantic_evidence(build.id))
        self._run_stage(build, "semantic_canonicalization", lambda: self._canonicalize_semantics(build.id))
        self._run_stage(
            build,
            "semantic_statistics",
            lambda: self._analyze_tables(("ontology_edges", "ontology_edge_evidence")),
        )
        self._run_stage(build, "validation", lambda: self._validate_graph(build.id))
        self._run_stage(build, "staging_cleanup", self._drop_staging_tables)

        node_count = self._count("ontology_nodes", build.id)
        edge_count = self._count("ontology_edges", build.id)
        evidence_count = self._count("ontology_edge_evidence", build.id)
        return node_count, edge_count, evidence_count

    def _validate_build(self, build: OntologyBuild) -> None:
        if build.engine_name != ONTOLOGY_ENGINE_NAME or build.schema_version != ONTOLOGY_SCHEMA_VERSION:
            raise ValueError(
                "V3 graph builder requires a V3-scoped build "
                f"engine={build.engine_name} schema={build.schema_version}"
            )
        if build.is_active:
            raise ValueError(f"active ontology builds are immutable build_id={build.id}")
        if build.status not in {"running", "failed"}:
            raise ValueError(f"ontology build is not writable build_id={build.id} status={build.status}")

    def _load_assets(self) -> dict[str, Any]:
        validate_assets(self.asset_dir)
        return load_assets(self.asset_dir)

    def _run_stage(
        self,
        build: OntologyBuild,
        name: str,
        operation: Callable[[], int],
    ) -> None:
        started = time.monotonic()
        self._stage_details = {}
        print(f"V3 ontology build stage={name} status=started", flush=True)
        try:
            self.db.execute(
                text("SELECT set_config('synchronous_commit', 'off', true)")
            )
            if name in {
                "ontology_catalog",
                "person_nodes",
                "semantic_evidence",
                "semantic_canonicalization",
                "validation",
            }:
                self.db.execute(
                    text("SELECT set_config('work_mem', :work_mem, true)"),
                    {"work_mem": ONTOLOGY_BUILD_WORK_MEM},
                )
            if name == "validation":
                self.db.execute(
                    text(
                        "SELECT set_config('max_parallel_workers_per_gather', '0', true)"
                    )
                )
            output_rows = operation()
            execution_finished = time.monotonic()
            self.db.commit()
            commit_finished = time.monotonic()
            metric = {
                "name": name,
                "elapsed_seconds": round(commit_finished - started, 6),
                "execution_seconds": round(execution_finished - started, 6),
                "commit_seconds": round(commit_finished - execution_finished, 6),
                "output_rows": output_rows,
                "status": "success",
                **self._stage_details,
            }
            self._save_stage_metric(build, metric)
            self.db.commit()
            print(
                f"V3 ontology build stage={name} status=success "
                f"rows={output_rows} elapsed_seconds={metric['elapsed_seconds']}",
                flush=True,
            )
        except Exception as exc:
            self.db.rollback()
            metric = {
                "name": name,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "output_rows": 0,
                "status": "failed",
                "error": str(exc)[:1000],
            }
            try:
                self._save_stage_metric(build, metric)
                self.db.commit()
            except Exception:
                self.db.rollback()
            print(
                f"V3 ontology build stage={name} status=failed "
                f"elapsed_seconds={metric['elapsed_seconds']}",
                flush=True,
            )
            raise

    def _save_stage_metric(self, build: OntologyBuild, metric: dict[str, Any]) -> None:
        self.stage_metrics.append(metric)
        build.properties = {
            **(build.properties or {}),
            "aggregation_policy_version": ONTOLOGY_AGGREGATION_POLICY_VERSION,
            "stage_metrics": list(self.stage_metrics),
        }
        self.db.add(build)

    def _delete_build_rows(self, table_name: str, build_id: int) -> int:
        allowed_tables = {table_name for table_name, _stage_name in INCOMPLETE_BUILD_TABLES}
        if table_name not in allowed_tables:
            raise ValueError(f"cannot reset unknown ontology table={table_name}")
        result = self.db.execute(
            text(f"DELETE FROM {table_name} WHERE build_id = :build_id"),
            {"build_id": build_id},
        )
        return int(result.rowcount or 0)

    def _create_catalog(self, build_id: int) -> int:
        table = self._catalog_name
        self.db.execute(text(f"DROP TABLE IF EXISTS {table}"))
        result = self.db.execute(
            text(
                f"""
                CREATE UNLOGGED TABLE {table} AS
                SELECT row_number() OVER (ORDER BY m.id)::bigint AS ordinal,
                       m.id AS movie_id
                FROM movies m
                WHERE m.adult IS FALSE
                  AND COALESCE(NULLIF(trim(m.title_ko), ''), NULLIF(trim(m.title), '')) IS NOT NULL
                """
            )
        )
        self.db.execute(text(f"CREATE UNIQUE INDEX ON {table} (movie_id)"))
        self.db.execute(text(f"CREATE UNIQUE INDEX ON {table} (ordinal)"))
        self.db.execute(text(f"ANALYZE {table}"))
        chunk_rows = self.db.execute(
            text(
                f"""
                SELECT min(ordinal), max(ordinal) + 1, min(movie_id), max(movie_id)
                FROM {table}
                GROUP BY ((ordinal - 1) / :batch_size)
                ORDER BY min(ordinal)
                """
            ),
            {"batch_size": self.batch_size},
        ).all()
        self._catalog_chunks = tuple(
            CatalogChunk(
                start_ordinal=int(row[0]),
                end_ordinal=int(row[1]),
                start_movie_id=int(row[2]),
                end_movie_id=int(row[3]),
            )
            for row in chunk_rows
        )
        return int(result.rowcount or 0)

    def _create_factual_nodes(
        self,
        build_id: int,
        *,
        node_stages: set[str] | None = None,
    ) -> int:
        catalog = self._catalog_name
        statements = {
            "movie_nodes": f"""
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            )
            SELECT :build_id, 'movie', m.id::text,
                   COALESCE(NULLIF(m.title_ko, ''), NULLIF(m.title, ''), m.id::text),
                   m.title_ko, m.title, 'db', 'movies', 1.0, true,
                   json_build_object('tmdb_id', m.tmdb_id, 'imdb_id', m.imdb_id), now(), now()
            FROM {catalog} c JOIN movies m ON m.id = c.movie_id
            """,
            "genre_nodes": f"""
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            )
            SELECT :build_id, 'genre', g.id::text,
                   COALESCE(NULLIF(g.name_ko, ''), g.name), g.name_ko, g.name,
                   'db', 'genres', 1.0, true, json_build_object('tmdb_id', g.tmdb_id), now(), now()
            FROM genres g
            JOIN (
                SELECT DISTINCT mg.genre_id
                FROM {catalog} c JOIN movie_genres mg ON mg.movie_id = c.movie_id
            ) linked ON linked.genre_id = g.id
            """,
            "keyword_nodes": f"""
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            )
            SELECT :build_id, 'keyword', k.id::text, k.name, NULL, k.name,
                   'db', 'keywords', 1.0, true, json_build_object('tmdb_id', k.tmdb_id), now(), now()
            FROM keywords k
            JOIN (
                SELECT DISTINCT mk.keyword_id
                FROM {catalog} c JOIN movie_keywords mk ON mk.movie_id = c.movie_id
            ) linked ON linked.keyword_id = k.id
            """,
            "person_nodes": f"""
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            )
            SELECT :build_id, 'person', p.id::text,
                   COALESCE(NULLIF(p.name_ko, ''), p.name), p.name_ko, p.name,
                   'db', 'people', 1.0, true, json_build_object('tmdb_id', p.tmdb_id), now(), now()
            FROM people p
            JOIN (
                SELECT ma.actor_id AS person_id FROM {catalog} c JOIN movie_actors ma ON ma.movie_id = c.movie_id
                UNION
                SELECT md.director_id AS person_id FROM {catalog} c JOIN movie_directors md ON md.movie_id = c.movie_id
            ) linked ON linked.person_id = p.id
            """,
            "ott_nodes": f"""
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            )
            SELECT :build_id, 'ott', o.id::text,
                   COALESCE(NULLIF(o.name_ko, ''), o.name), o.name_ko, o.name,
                   'db', 'otts', 1.0, true, json_build_object('tmdb_id', o.tmdb_id), now(), now()
            FROM otts o
            JOIN (
                SELECT DISTINCT mo.ott_id
                FROM {catalog} c JOIN movie_otts mo ON mo.movie_id = c.movie_id
            ) linked ON linked.ott_id = o.id
            """,
        }
        selected_stages = node_stages or set(statements)
        unknown_stages = selected_stages - set(statements)
        if unknown_stages:
            raise ValueError(f"unknown ontology node stages stages={sorted(unknown_stages)}")
        return self._execute_all(
            tuple(statements[stage] for stage in statements if stage in selected_stages),
            build_id=build_id,
        )

    def _create_concept_nodes(self, build_id: int, assets: dict[str, Any]) -> int:
        inserted = 0
        statement = text(
            """
            INSERT INTO ontology_nodes (
                build_id, node_type, ref_id, label, label_ko, label_en, source,
                source_table, confidence, is_active, properties, created_at, updated_at
            ) VALUES (
                :build_id, :node_type, :ref_id, :label, :label_ko, :label_en,
                'manual_asset', NULL, 1.0, true, CAST(:properties AS JSON), now(), now()
            )
            """
        )
        for node_type, filename in (("theme", "themes.json"), ("mood", "moods.json")):
            for item in assets[filename]["items"]:
                result = self.db.execute(
                    statement,
                    {
                        "build_id": build_id,
                        "node_type": node_type,
                        "ref_id": item["key"],
                        "label": item.get("label_ko") or item.get("label_en") or item["key"],
                        "label_ko": item.get("label_ko"),
                        "label_en": item.get("label_en"),
                        "properties": json.dumps(
                            {
                                "aliases": item.get("aliases", []),
                                "description": item.get("description"),
                                "asset_version": assets[filename].get("version"),
                            }
                        ),
                    },
                )
                inserted += int(result.rowcount or 0)
        return inserted

    def _create_factual_edges(
        self,
        build_id: int,
        *,
        relation_types: set[str] | None = None,
    ) -> int:
        catalog = self._catalog_name
        mappings = (
            ("movie_genres", "genre_id", "genre", "has_genre", None),
            ("movie_keywords", "keyword_id", "keyword", "has_keyword", None),
            ("movie_actors", "actor_id", "person", "has_actor", None),
            ("movie_directors", "director_id", "person", "has_director", None),
            ("movie_otts", "ott_id", "ott", "available_streaming_on", "is_streaming"),
            ("movie_otts", "ott_id", "ott", "available_rent_on", "is_rent"),
            ("movie_otts", "ott_id", "ott", "available_buy_on", "is_buy"),
        )
        selected_relations = relation_types or {mapping[3] for mapping in mappings}
        unknown_relations = selected_relations - {mapping[3] for mapping in mappings}
        if unknown_relations:
            raise ValueError(
                f"unknown factual ontology relations relations={sorted(unknown_relations)}"
            )
        inserted = 0
        relation_metrics: list[dict[str, Any]] = []
        for table_name, target_column, target_type, relation_type, flag_column in mappings:
            if relation_type not in selected_relations:
                continue
            get_relation_definition(
                relation_type,
                source_type=NodeType.MOVIE,
                target_type=target_type,
            )
            flag_clause = f"AND mapping.{flag_column} IS TRUE" if flag_column else ""
            statement = text(
                f"""
                    INSERT INTO ontology_edges (
                        build_id, source_node_id, target_node_id, relation_type, weight,
                        confidence, effective_strength, evidence_count, source, properties, created_at
                    )
                    SELECT :build_id, movie_node.id, target_node.id, :relation_type,
                           1.0, 1.0, 1.0, 0, 'db',
                           json_build_object('source_table', :table_name), now()
                    FROM {catalog} c
                    JOIN {table_name} mapping ON mapping.movie_id = c.movie_id
                    JOIN ontology_nodes movie_node
                      ON movie_node.build_id = :build_id AND movie_node.node_type = 'movie'
                     AND movie_node.ref_id = mapping.movie_id::text
                    JOIN ontology_nodes target_node
                      ON target_node.build_id = :build_id AND target_node.node_type = :target_type
                     AND target_node.ref_id = mapping.{target_column}::text
                    WHERE c.ordinal >= :start_ordinal
                      AND c.ordinal < :end_ordinal
                      AND mapping.movie_id >= :start_movie_id
                      AND mapping.movie_id <= :end_movie_id
                      {flag_clause}
                """
            )

            def insert_chunk(worker_db: Session, chunk: CatalogChunk) -> int:
                result = worker_db.execute(
                    statement,
                    {
                        "build_id": build_id,
                        "relation_type": relation_type,
                        "target_type": target_type,
                        "table_name": table_name,
                        "start_ordinal": chunk.start_ordinal,
                        "end_ordinal": chunk.end_ordinal,
                        "start_movie_id": chunk.start_movie_id,
                        "end_movie_id": chunk.end_movie_id,
                    },
                )
                return int(result.rowcount or 0)

            relation_rows, parallel_metric = self._run_catalog_chunk_queue(
                relation_type,
                insert_chunk,
            )
            inserted += relation_rows
            relation_metrics.append(parallel_metric)
        self._stage_details["parallelism"] = relation_metrics
        return inserted

    def _run_catalog_chunk_queue(
        self,
        operation_name: str,
        operation: Callable[[Session, CatalogChunk], int],
    ) -> tuple[int, dict[str, Any]]:
        chunks = self._catalog_chunks
        if not chunks:
            return 0, {
                "operation": operation_name,
                "worker_count": 0,
                "chunk_size": self.batch_size,
                "chunk_count": 0,
                "worker_metrics": [],
            }

        worker_count = min(self.worker_count, len(chunks))
        if worker_count == 1:
            started = time.monotonic()
            rows = sum(operation(self.db, chunk) for chunk in chunks)
            elapsed = time.monotonic() - started
            metric = ParallelWorkerMetric(
                worker_id=1,
                chunks=len(chunks),
                rows=rows,
                active_seconds=round(elapsed, 6),
                elapsed_seconds=round(elapsed, 6),
            )
            return rows, {
                "operation": operation_name,
                "worker_count": 1,
                "chunk_size": self.batch_size,
                "chunk_count": len(chunks),
                "worker_metrics": [asdict(metric)],
            }

        work_queue: queue.Queue[CatalogChunk] = queue.Queue()
        for chunk in chunks:
            work_queue.put(chunk)
        stop_event = threading.Event()

        def run_worker(worker_id: int) -> ParallelWorkerMetric:
            chunk_count = 0
            row_count = 0
            active_seconds = 0.0
            worker_started = time.monotonic()
            with self.session_factory() as worker_db:
                worker_db.execute(
                    text("SELECT set_config('synchronous_commit', 'off', false)")
                )
                worker_db.execute(
                    text("SELECT set_config('work_mem', :work_mem, false)"),
                    {"work_mem": ONTOLOGY_BUILD_WORK_MEM},
                )
                worker_db.execute(
                    text("SELECT set_config('application_name', :name, false)"),
                    {"name": f"v3-ontology-{operation_name}-{worker_id}"},
                )
                while not stop_event.is_set():
                    try:
                        chunk = work_queue.get_nowait()
                    except queue.Empty:
                        break
                    chunk_started = time.monotonic()
                    try:
                        rows = operation(worker_db, chunk)
                        worker_db.commit()
                    except Exception:
                        worker_db.rollback()
                        stop_event.set()
                        raise
                    finally:
                        work_queue.task_done()
                    active_seconds += time.monotonic() - chunk_started
                    chunk_count += 1
                    row_count += rows
            return ParallelWorkerMetric(
                worker_id=worker_id,
                chunks=chunk_count,
                rows=row_count,
                active_seconds=round(active_seconds, 6),
                elapsed_seconds=round(time.monotonic() - worker_started, 6),
            )

        metrics: list[ParallelWorkerMetric] = []
        first_error: BaseException | None = None
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix=f"v3-ontology-{operation_name}",
        ) as executor:
            futures = [executor.submit(run_worker, index + 1) for index in range(worker_count)]
            for future in futures:
                try:
                    metrics.append(future.result())
                except BaseException as exc:
                    stop_event.set()
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

        rows = sum(metric.rows for metric in metrics)
        return rows, {
            "operation": operation_name,
            "worker_count": worker_count,
            "chunk_size": self.batch_size,
            "chunk_count": len(chunks),
            "worker_metrics": [asdict(metric) for metric in metrics],
        }

    def _create_asset_edges(self, build_id: int, assets: dict[str, Any]) -> int:
        inserted = 0
        unresolved_relations: list[str] = []
        filenames = (
            "genre_theme_mood_rules.json",
            "keyword_theme_mood_rules.json",
            "theme_relations.json",
            "mood_relations.json",
        )
        for filename in filenames:
            for index, relation in enumerate(assets[filename].get("relations", [])):
                relation_type = LEGACY_RELATION_NAMES.get(
                    relation["relation_type"], relation["relation_type"]
                )
                definition = get_relation_definition(
                    relation_type,
                    source_type=relation["source_type"],
                    target_type=relation["target_type"],
                )
                source_id = self._resolve_asset_node(
                    build_id, definition.source_type, relation["source_key"]
                )
                target_id = self._resolve_asset_node(
                    build_id, definition.target_type, relation["target_key"]
                )
                if source_id is None or target_id is None:
                    unresolved_relations.append(
                        f"{filename}:{index} "
                        f"{relation['source_type']}:{relation['source_key']} "
                        f"{relation_type} "
                        f"{relation['target_type']}:{relation['target_key']}"
                    )
                    continue
                inserted += self._insert_asset_edge(
                    build_id=build_id,
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=relation_type,
                    relation=relation,
                    source_ref=f"{filename}:{index}",
                )
                if definition.symmetric and source_id != target_id:
                    inserted += self._insert_asset_edge(
                        build_id=build_id,
                        source_id=target_id,
                        target_id=source_id,
                        relation_type=relation_type,
                        relation=relation,
                        source_ref=f"{filename}:{index}:inverse",
                    )
        if unresolved_relations and self.strict_asset_resolution:
            raise ValueError(
                "V3 ontology asset relations reference unresolved nodes "
                f"relations={unresolved_relations}"
            )
        return inserted

    def _resolve_asset_node(
        self,
        build_id: int,
        node_type: NodeType,
        key: str,
    ) -> int | None:
        if node_type in {NodeType.THEME, NodeType.MOOD}:
            clause = "ref_id = :key"
        else:
            clause = "(lower(label) = lower(:key) OR lower(coalesce(label_en, '')) = lower(:key) OR lower(coalesce(label_ko, '')) = lower(:key))"
        return self.db.execute(
            text(
                f"""
                SELECT id FROM ontology_nodes
                WHERE build_id = :build_id AND node_type = :node_type AND {clause}
                LIMIT 1
                """
            ),
            {"build_id": build_id, "node_type": node_type.value, "key": key},
        ).scalar_one_or_none()

    def _insert_asset_edge(
        self,
        *,
        build_id: int,
        source_id: int,
        target_id: int,
        relation_type: str,
        relation: dict[str, Any],
        source_ref: str,
    ) -> int:
        weight = float(relation["weight"])
        confidence = float(relation["confidence"])
        result = self.db.execute(
            text(
                """
                INSERT INTO ontology_edges (
                    build_id, source_node_id, target_node_id, relation_type, weight,
                    confidence, effective_strength, evidence_count, source, properties, created_at
                ) VALUES (
                    :build_id, :source_id, :target_id, :relation_type, :weight,
                    :confidence, :effective_strength, 0, 'manual_asset', CAST(:properties AS JSON), now()
                ) ON CONFLICT (build_id, source_node_id, target_node_id, relation_type) DO NOTHING
                """
            ),
            {
                "build_id": build_id,
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "weight": weight,
                "confidence": confidence,
                "effective_strength": weight * confidence,
                "properties": json.dumps(
                    {
                        "source_ref": source_ref,
                        "source_type": relation.get("source_type"),
                        "source_key": relation.get("source_key"),
                        "target_type": relation.get("target_type"),
                        "target_key": relation.get("target_key"),
                        "description": relation.get("description"),
                        "asset_version": relation.get("version"),
                    }
                ),
            },
        )
        return int(result.rowcount or 0)

    def _create_semantic_evidence(self, build_id: int) -> int:
        stage = self._evidence_stage_name
        self.db.execute(text(f"DROP TABLE IF EXISTS {stage}"))
        self.db.execute(
            text(
                f"""
                CREATE UNLOGGED TABLE {stage} (
                    source_node_id integer NOT NULL,
                    target_node_id integer NOT NULL,
                    relation_type varchar(80) NOT NULL,
                    evidence_type varchar(50) NOT NULL,
                    source_ref varchar(255) NOT NULL,
                    path jsonb NOT NULL,
                    raw_weight double precision NOT NULL,
                    confidence double precision NOT NULL,
                    effective_strength double precision NOT NULL,
                    properties jsonb
                )
                """
            )
        )
        inserted = 0
        asset_result = self.db.execute(
            text(
                f"""
                INSERT INTO {stage}
                SELECT movie_edge.source_node_id,
                       semantic_edge.target_node_id,
                       CASE semantic_edge.relation_type
                           WHEN 'suggests_theme' THEN 'has_theme'
                           WHEN 'suggests_mood' THEN 'has_mood'
                       END,
                       CASE movie_edge.relation_type
                           WHEN 'has_genre' THEN 'genre_rule'
                           WHEN 'has_keyword' THEN 'keyword_rule'
                       END,
                       concat(movie_edge.relation_type, ':', movie_edge.target_node_id, ':', semantic_edge.id),
                       jsonb_build_array(movie_edge.source_node_id, movie_edge.target_node_id, semantic_edge.target_node_id),
                       movie_edge.weight * semantic_edge.weight,
                       movie_edge.confidence * semantic_edge.confidence,
                       movie_edge.weight * semantic_edge.weight * movie_edge.confidence * semantic_edge.confidence,
                       jsonb_build_object('semantic_edge_id', semantic_edge.id)
                FROM ontology_edges movie_edge
                JOIN ontology_edges semantic_edge
                  ON semantic_edge.build_id = :build_id
                 AND semantic_edge.source_node_id = movie_edge.target_node_id
                 AND semantic_edge.relation_type IN ('suggests_theme', 'suggests_mood')
                WHERE movie_edge.build_id = :build_id
                  AND movie_edge.relation_type IN ('has_genre', 'has_keyword')
                """
            ),
            {"build_id": build_id},
        )
        inserted += int(asset_result.rowcount or 0)

        overview_result = self.db.execute(
            text(
                f"""
                INSERT INTO {stage}
                SELECT movie_node.id, target_node.id,
                       CASE signal.signal_type WHEN 'theme' THEN 'has_theme' WHEN 'mood' THEN 'has_mood' END,
                       'overview_signal',
                       concat('overview:', signal.id),
                       jsonb_build_array(movie_node.id, target_node.id),
                       signal.weight, signal.confidence, signal.weight * signal.confidence,
                       jsonb_build_object(
                           'matched_terms', signal.matched_terms,
                           'overview_hash', signal.overview_hash,
                           'extractor_version', signal.extractor_version
                       )
                FROM movie_overview_semantic_signals signal
                JOIN ontology_nodes movie_node
                  ON movie_node.build_id = :build_id AND movie_node.node_type = 'movie'
                 AND movie_node.ref_id = signal.movie_id::text
                JOIN ontology_nodes target_node
                  ON target_node.build_id = :build_id AND target_node.node_type = signal.signal_type
                 AND target_node.ref_id = signal.signal_key
                WHERE signal.signal_type IN ('theme', 'mood')
                """
            ),
            {"build_id": build_id},
        )
        inserted += int(overview_result.rowcount or 0)

        mood_result = self.db.execute(
            text(
                f"""
                INSERT INTO {stage}
                SELECT evidence.source_node_id, semantic_edge.target_node_id, 'has_mood',
                       'theme_to_mood_rule',
                       left(concat(evidence.source_ref, ':', semantic_edge.id), 255),
                       evidence.path || jsonb_build_array(semantic_edge.target_node_id),
                       evidence.raw_weight * semantic_edge.weight,
                       evidence.confidence * semantic_edge.confidence,
                       evidence.effective_strength * semantic_edge.weight * semantic_edge.confidence,
                       jsonb_build_object('semantic_edge_id', semantic_edge.id)
                FROM {stage} evidence
                JOIN ontology_edges semantic_edge
                  ON semantic_edge.build_id = :build_id
                 AND semantic_edge.source_node_id = evidence.target_node_id
                 AND semantic_edge.relation_type = 'evokes_mood'
                WHERE evidence.relation_type = 'has_theme'
                """
            ),
            {"build_id": build_id},
        )
        inserted += int(mood_result.rowcount or 0)
        self.db.execute(
            text(
                f"CREATE INDEX ON {stage} (source_node_id, target_node_id, relation_type, evidence_type)"
            )
        )
        return inserted

    def _canonicalize_semantics(self, build_id: int) -> int:
        stage = self._evidence_stage_name
        edge_result = self.db.execute(
            text(
                f"""
                WITH family AS (
                    SELECT source_node_id, target_node_id, relation_type, evidence_type,
                           max(effective_strength) AS family_strength
                    FROM {stage}
                    GROUP BY source_node_id, target_node_id, relation_type, evidence_type
                ), combined AS (
                    SELECT source_node_id, target_node_id, relation_type,
                           CASE
                               WHEN bool_or(family_strength >= 1.0) THEN 1.0
                               ELSE 1.0 - exp(sum(ln(1.0 - family_strength)))
                           END AS effective_strength,
                           sum(1) AS family_count
                    FROM family
                    GROUP BY source_node_id, target_node_id, relation_type
                ), evidence_counts AS (
                    SELECT source_node_id, target_node_id, relation_type, count(*) AS evidence_count
                    FROM {stage}
                    GROUP BY source_node_id, target_node_id, relation_type
                )
                INSERT INTO ontology_edges (
                    build_id, source_node_id, target_node_id, relation_type, weight,
                    confidence, effective_strength, evidence_count, source, properties, created_at
                )
                SELECT :build_id, combined.source_node_id, combined.target_node_id,
                       combined.relation_type, combined.effective_strength, 1.0,
                       combined.effective_strength, evidence_counts.evidence_count,
                       'semantic_aggregate',
                       json_build_object(
                           'aggregation_policy_version', :aggregation_policy_version,
                           'family_count', combined.family_count
                       ), now()
                FROM combined
                JOIN evidence_counts USING (source_node_id, target_node_id, relation_type)
                """
            ),
            {
                "build_id": build_id,
                "aggregation_policy_version": ONTOLOGY_AGGREGATION_POLICY_VERSION,
            },
        )
        edge_count = int(edge_result.rowcount or 0)
        evidence_result = self.db.execute(
            text(
                f"""
                INSERT INTO ontology_edge_evidence (
                    build_id, edge_id, evidence_type, source_ref, path, raw_weight,
                    confidence, effective_strength, properties, created_at
                )
                SELECT :build_id, edge.id, evidence.evidence_type, evidence.source_ref,
                       evidence.path, evidence.raw_weight, evidence.confidence,
                       evidence.effective_strength, evidence.properties, now()
                FROM {stage} evidence
                JOIN ontology_edges edge
                  ON edge.build_id = :build_id
                 AND edge.source_node_id = evidence.source_node_id
                 AND edge.target_node_id = evidence.target_node_id
                 AND edge.relation_type = evidence.relation_type
                ON CONFLICT (build_id, edge_id, evidence_type, source_ref) DO NOTHING
                """
            ),
            {"build_id": build_id},
        )
        return edge_count + int(evidence_result.rowcount or 0)

    def _validate_graph(self, build_id: int) -> int:
        node_counts = dict(
            self.db.execute(
                text(
                    """
                    SELECT node_type, count(*)
                    FROM ontology_nodes
                    WHERE build_id = :build_id
                    GROUP BY node_type
                    """
                ),
                {"build_id": build_id},
            ).all()
        )
        required_node_types = {node_type.value for node_type in NodeType}
        missing_node_types = sorted(
            node_type for node_type in required_node_types if node_counts.get(node_type, 0) == 0
        )
        legacy_role_nodes = {
            node_type: node_counts[node_type]
            for node_type in ("actor", "director")
            if node_counts.get(node_type, 0) > 0
        }
        if missing_node_types or legacy_role_nodes:
            raise ValueError(
                "invalid V3 ontology node coverage "
                f"missing={missing_node_types} legacy_role_nodes={legacy_role_nodes}"
            )

        invalid_edge_count = int(
            self.db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM ontology_edges
                    WHERE build_id = :build_id
                      AND (
                          weight < 0 OR weight > 1
                          OR confidence < 0 OR confidence > 1
                          OR effective_strength IS NULL
                          OR effective_strength < 0 OR effective_strength > 1
                          OR evidence_count < 0
                      )
                    """
                ),
                {"build_id": build_id},
            ).scalar_one()
        )
        if invalid_edge_count:
            raise ValueError(f"invalid V3 ontology edges count={invalid_edge_count}")

        endpoint_rows = self.db.execute(
            text(
                """
                SELECT edge.relation_type,
                       source.node_type,
                       target.node_type,
                       count(*) AS edge_count
                FROM ontology_edges edge
                JOIN ontology_nodes source
                  ON source.id = edge.source_node_id
                 AND source.build_id = :build_id
                JOIN ontology_nodes target
                  ON target.id = edge.target_node_id
                 AND target.build_id = :build_id
                WHERE edge.build_id = :build_id
                GROUP BY edge.relation_type, source.node_type, target.node_type
                """
            ),
            {"build_id": build_id},
        ).all()
        relation_counts: dict[str, int] = {}
        endpoint_edge_count = 0
        for relation_type, source_type, target_type, edge_count in endpoint_rows:
            relation_counts[relation_type] = (
                relation_counts.get(relation_type, 0) + int(edge_count)
            )
            endpoint_edge_count += int(edge_count)
            definition = get_relation_definition(
                relation_type,
                source_type=source_type,
                target_type=target_type,
            )
            if not definition.active:
                raise ValueError(f"inactive relation was materialized relation={relation_type}")
        required_relations = {
            "has_genre",
            "has_keyword",
            "has_actor",
            "has_director",
            "available_streaming_on",
            "suggests_theme",
            "suggests_mood",
            "has_theme",
            "has_mood",
        }
        missing_relations = sorted(
            relation for relation in required_relations if relation_counts.get(relation, 0) == 0
        )
        if missing_relations:
            raise ValueError(f"required V3 ontology relations are empty relations={missing_relations}")
        total_edge_count = self._count("ontology_edges", build_id)
        if endpoint_edge_count != total_edge_count:
            raise ValueError(
                "ontology edges reference nodes from another build "
                f"endpoint_edges={endpoint_edge_count} total_edges={total_edge_count}"
            )

        semantic_mismatch_count = int(
            self.db.execute(
                text(
                    """
                    WITH evidence_counts AS (
                        SELECT edge_id, count(*) AS evidence_count
                        FROM ontology_edge_evidence
                        WHERE build_id = :build_id
                        GROUP BY edge_id
                    )
                    SELECT count(*)
                    FROM ontology_edges edge
                    LEFT JOIN evidence_counts evidence ON evidence.edge_id = edge.id
                    WHERE edge.build_id = :build_id
                      AND edge.relation_type IN ('has_theme', 'has_mood')
                      AND (
                          edge.evidence_count = 0
                          OR edge.evidence_count <> COALESCE(evidence.evidence_count, 0)
                      )
                    """
                ),
                {"build_id": build_id},
            ).scalar_one()
        )
        if semantic_mismatch_count:
            raise ValueError(
                "canonical semantic edges do not match evidence rows "
                f"count={semantic_mismatch_count}"
            )

        overview_evidence_count = int(
            self.db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM ontology_edge_evidence
                    WHERE build_id = :build_id AND evidence_type = 'overview_signal'
                    """
                ),
                {"build_id": build_id},
            ).scalar_one()
        )
        if overview_evidence_count == 0:
            raise ValueError("V3 ontology build has no overview semantic evidence")

        evidence_build_mismatch_count = int(
            self.db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM ontology_edge_evidence evidence
                    JOIN ontology_edges edge ON edge.id = evidence.edge_id
                    WHERE evidence.build_id = :build_id
                      AND edge.build_id <> evidence.build_id
                    """
                ),
                {"build_id": build_id},
            ).scalar_one()
        )
        if evidence_build_mismatch_count:
            raise ValueError(
                "ontology evidence references an edge from another build "
                f"count={evidence_build_mismatch_count}"
            )
        return len(endpoint_rows)

    def _drop_staging_tables(self) -> int:
        self.db.execute(text(f"DROP TABLE IF EXISTS {self._evidence_stage_name}"))
        self.db.execute(text(f"DROP TABLE IF EXISTS {self._catalog_name}"))
        self._catalog_chunks = ()
        return 2

    def _analyze_tables(self, table_names: tuple[str, ...]) -> int:
        allowed_tables = {
            "ontology_nodes",
            "ontology_edges",
            "ontology_edge_evidence",
        }
        unknown_tables = set(table_names) - allowed_tables
        if unknown_tables:
            raise ValueError(f"cannot analyze unknown ontology tables={sorted(unknown_tables)}")
        for table_name in table_names:
            self.db.execute(text(f"ANALYZE {table_name}"))
        return len(table_names)

    def _execute_all(self, statements: tuple[str, ...], *, build_id: int) -> int:
        total = 0
        for statement in statements:
            result = self.db.execute(text(statement), {"build_id": build_id})
            total += int(result.rowcount or 0)
        return total

    def _count(self, table_name: str, build_id: int) -> int:
        return int(
            self.db.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE build_id = :build_id"),
                {"build_id": build_id},
            ).scalar_one()
        )

    @property
    def _catalog_name(self) -> str:
        if self._catalog_table is None:
            raise RuntimeError("ontology catalog has not been initialized")
        return self._catalog_table

    @property
    def _evidence_stage_name(self) -> str:
        if self._evidence_stage_table is None:
            raise RuntimeError("ontology evidence stage has not been initialized")
        return self._evidence_stage_table
