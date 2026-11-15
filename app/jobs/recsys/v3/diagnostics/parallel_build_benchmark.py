from __future__ import annotations

import argparse
import json
import queue
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


CATALOG_TABLE = "v3_parallel_benchmark_catalog"
EDGE_TABLE = "v3_parallel_benchmark_edges"
LOCK_KEY = "v3_parallel_build_benchmark"
BENCHMARK_BUILD_ID = 1


@dataclass(frozen=True)
class WorkerMetric:
    worker_id: int
    chunks: int
    rows: int
    active_seconds: float
    elapsed_seconds: float


def _active_v2_build_id() -> int:
    with SessionLocal() as db:
        build_id = db.execute(
            text(
                """
                SELECT id
                FROM ontology_builds
                WHERE engine_name = 'v2'
                  AND schema_version = 'v2'
                  AND is_active IS TRUE
                ORDER BY id DESC
                LIMIT 1
                """
            )
        ).scalar_one_or_none()
    if build_id is None:
        raise RuntimeError("an active V2 ontology build is required for the benchmark")
    return int(build_id)


def _acquire_lock() -> Session:
    db = SessionLocal()
    try:
        acquired = db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": LOCK_KEY},
        ).scalar_one()
        if not acquired:
            raise RuntimeError("another V3 parallel benchmark is already running")
        return db
    except Exception:
        db.close()
        raise


def _prepare_catalog(catalog_size: int) -> int:
    with SessionLocal.begin() as db:
        db.execute(text(f"DROP TABLE IF EXISTS {CATALOG_TABLE}"))
        result = db.execute(
            text(
                f"""
                CREATE UNLOGGED TABLE {CATALOG_TABLE} AS
                SELECT row_number() OVER (ORDER BY selected.movie_id)::bigint AS ordinal,
                       selected.movie_id
                FROM (
                    SELECT m.id AS movie_id
                    FROM movies m
                    WHERE m.adult IS FALSE
                      AND COALESCE(
                          NULLIF(trim(m.title_ko), ''),
                          NULLIF(trim(m.title), '')
                      ) IS NOT NULL
                    ORDER BY m.id
                    LIMIT :catalog_size
                ) selected
                """
            ),
            {"catalog_size": catalog_size},
        )
        db.execute(text(f"ALTER TABLE {CATALOG_TABLE} ADD PRIMARY KEY (ordinal)"))
        db.execute(
            text(f"CREATE UNIQUE INDEX ON {CATALOG_TABLE} (movie_id)")
        )
        db.execute(text(f"ANALYZE {CATALOG_TABLE}"))
        return int(result.rowcount or 0)


def _prepare_edge_table() -> None:
    with SessionLocal.begin() as db:
        db.execute(text(f"DROP TABLE IF EXISTS {EDGE_TABLE}"))
        db.execute(
            text(
                f"""
                CREATE TABLE {EDGE_TABLE} (
                    id bigserial PRIMARY KEY,
                    build_id integer NOT NULL,
                    source_node_id integer NOT NULL,
                    target_node_id integer NOT NULL,
                    relation_type varchar(80) NOT NULL,
                    weight double precision NOT NULL,
                    confidence double precision NOT NULL,
                    effective_strength double precision,
                    evidence_count integer NOT NULL DEFAULT 0,
                    source varchar(50) NOT NULL,
                    properties json,
                    created_at timestamp without time zone NOT NULL DEFAULT now(),
                    UNIQUE (build_id, source_node_id, target_node_id, relation_type)
                )
                """
            )
        )
        db.execute(
            text(
                f"CREATE INDEX ON {EDGE_TABLE} "
                "(build_id, relation_type, source_node_id)"
            )
        )
        db.execute(
            text(
                f"CREATE INDEX ON {EDGE_TABLE} "
                "(build_id, relation_type, target_node_id)"
            )
        )
        db.execute(
            text(f"CREATE INDEX ON {EDGE_TABLE} (build_id, target_node_id)")
        )
        db.execute(text(f"CREATE INDEX ON {EDGE_TABLE} (source_node_id)"))
        db.execute(text(f"CREATE INDEX ON {EDGE_TABLE} (target_node_id)"))


def _drop_benchmark_tables() -> None:
    with SessionLocal.begin() as db:
        db.execute(text(f"DROP TABLE IF EXISTS {EDGE_TABLE}"))
        db.execute(text(f"DROP TABLE IF EXISTS {CATALOG_TABLE}"))


def _wal_lsn() -> str:
    with SessionLocal() as db:
        return str(db.execute(text("SELECT pg_current_wal_lsn()::text")).scalar_one())


def _wal_bytes(start_lsn: str, end_lsn: str) -> int:
    with SessionLocal() as db:
        value = db.execute(
            text("SELECT pg_wal_lsn_diff(CAST(:end AS pg_lsn), CAST(:start AS pg_lsn))"),
            {"start": start_lsn, "end": end_lsn},
        ).scalar_one()
        return int(value)


def _table_bytes() -> int:
    with SessionLocal() as db:
        return int(
            db.execute(
                text("SELECT pg_total_relation_size(CAST(:table_name AS regclass))"),
                {"table_name": EDGE_TABLE},
            ).scalar_one()
        )


def _worker(
    worker_id: int,
    work_queue: queue.Queue[tuple[int, int]],
    barrier: threading.Barrier,
    source_build_id: int,
) -> WorkerMetric:
    chunks = 0
    rows = 0
    active_seconds = 0.0
    barrier.wait()
    worker_started = time.perf_counter()
    with SessionLocal() as db:
        db.execute(text("SET synchronous_commit TO OFF"))
        db.execute(text("SET work_mem TO '64MB'"))
        db.execute(
            text("SELECT set_config('application_name', :name, false)"),
            {"name": f"v3-benchmark-worker-{worker_id}"},
        )
        while True:
            try:
                start_ordinal, end_ordinal = work_queue.get_nowait()
            except queue.Empty:
                break

            chunk_started = time.perf_counter()
            result = db.execute(
                text(
                    f"""
                    INSERT INTO {EDGE_TABLE} (
                        build_id, source_node_id, target_node_id, relation_type,
                        weight, confidence, effective_strength, evidence_count,
                        source, properties, created_at
                    )
                    SELECT :benchmark_build_id, movie_node.id, actor_node.id,
                           'has_actor', 1.0, 1.0, 1.0, 0, 'benchmark',
                           json_build_object('source_table', 'movie_actors'), now()
                    FROM {CATALOG_TABLE} c
                    JOIN movie_actors mapping ON mapping.movie_id = c.movie_id
                    JOIN ontology_nodes movie_node
                      ON movie_node.build_id = :source_build_id
                     AND movie_node.node_type = 'movie'
                     AND movie_node.ref_id = mapping.movie_id::text
                    JOIN ontology_nodes actor_node
                      ON actor_node.build_id = :source_build_id
                     AND actor_node.node_type = 'actor'
                     AND actor_node.ref_id = mapping.actor_id::text
                    WHERE c.ordinal >= :start_ordinal
                      AND c.ordinal < :end_ordinal
                    """
                ),
                {
                    "benchmark_build_id": BENCHMARK_BUILD_ID,
                    "source_build_id": source_build_id,
                    "start_ordinal": start_ordinal,
                    "end_ordinal": end_ordinal,
                },
            )
            db.commit()
            active_seconds += time.perf_counter() - chunk_started
            chunks += 1
            rows += int(result.rowcount or 0)
            work_queue.task_done()

    return WorkerMetric(
        worker_id=worker_id,
        chunks=chunks,
        rows=rows,
        active_seconds=round(active_seconds, 6),
        elapsed_seconds=round(time.perf_counter() - worker_started, 6),
    )


def _run_trial(
    workers: int,
    *,
    trial: int,
    catalog_count: int,
    chunk_size: int,
    source_build_id: int,
) -> dict[str, object]:
    _prepare_edge_table()
    work_queue: queue.Queue[tuple[int, int]] = queue.Queue()
    for start_ordinal in range(1, catalog_count + 1, chunk_size):
        work_queue.put((start_ordinal, min(start_ordinal + chunk_size, catalog_count + 1)))

    barrier = threading.Barrier(workers + 1)
    start_lsn = _wal_lsn()
    print(
        f"BENCHMARK_TRIAL_START trial={trial} workers={workers} "
        f"catalog_movies={catalog_count} chunk_size={chunk_size}",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_worker, worker_id, work_queue, barrier, source_build_id)
            for worker_id in range(1, workers + 1)
        ]
        started = time.perf_counter()
        barrier.wait()
        metrics = [future.result() for future in futures]
    elapsed = time.perf_counter() - started
    end_lsn = _wal_lsn()

    with SessionLocal() as db:
        actual_rows = int(
            db.execute(text(f"SELECT count(*) FROM {EDGE_TABLE}")).scalar_one()
        )
    reported_rows = sum(metric.rows for metric in metrics)
    if actual_rows != reported_rows:
        raise RuntimeError(
            f"row count mismatch actual={actual_rows} reported={reported_rows}"
        )

    active_times = [metric.active_seconds for metric in metrics]
    result: dict[str, object] = {
        "trial": trial,
        "workers": workers,
        "catalog_movies": catalog_count,
        "chunk_size": chunk_size,
        "chunks": sum(metric.chunks for metric in metrics),
        "rows": actual_rows,
        "elapsed_seconds": round(elapsed, 6),
        "rows_per_second": round(actual_rows / elapsed, 2),
        "wal_bytes": _wal_bytes(start_lsn, end_lsn),
        "table_bytes": _table_bytes(),
        "worker_active_spread_seconds": round(max(active_times) - min(active_times), 6),
        "worker_metrics": [asdict(metric) for metric in metrics],
    }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return result


def _summarize(results: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for workers in sorted({int(result["workers"]) for result in results}):
        matching = [result for result in results if int(result["workers"]) == workers]
        summary[str(workers)] = {
            "median_elapsed_seconds": round(
                statistics.median(float(result["elapsed_seconds"]) for result in matching),
                6,
            ),
            "median_rows_per_second": round(
                statistics.median(float(result["rows_per_second"]) for result in matching),
                2,
            ),
            "median_wal_bytes": int(
                statistics.median(int(result["wal_bytes"]) for result in matching)
            ),
            "trials": len(matching),
        }
    return summary


def run_benchmark(
    catalog_size: int,
    chunk_size: int,
    workers_order: tuple[int, ...] = (2, 4, 4, 2),
) -> dict[str, object]:
    if catalog_size <= 0 or chunk_size <= 0:
        raise ValueError("catalog_size and chunk_size must be positive")
    if not workers_order or any(workers <= 0 for workers in workers_order):
        raise ValueError("workers_order must contain positive worker counts")

    lock_db = _acquire_lock()
    try:
        source_build_id = _active_v2_build_id()
        catalog_count = _prepare_catalog(catalog_size)
        if catalog_count == 0:
            raise RuntimeError("benchmark catalog is empty")
        results = []
        for trial, workers in enumerate(workers_order, start=1):
            results.append(
                _run_trial(
                    workers,
                    trial=trial,
                    catalog_count=catalog_count,
                    chunk_size=chunk_size,
                    source_build_id=source_build_id,
                )
            )
        payload = {
            "source_build_id": source_build_id,
            "scheduler": "shared_dynamic_chunk_queue",
            "results": results,
            "summary": _summarize(results),
        }
        print("BENCHMARK_SUMMARY=" + json.dumps(payload, ensure_ascii=True), flush=True)
        return payload
    finally:
        try:
            _drop_benchmark_tables()
        finally:
            lock_db.rollback()
            lock_db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare 2 and 4 graph-build workers")
    parser.add_argument("--catalog-size", type=int, default=25_000)
    parser.add_argument("--chunk-size", type=int, default=1_000)
    parser.add_argument("--workers-order", default="2,4,4,2")
    args = parser.parse_args()
    workers_order = tuple(int(value) for value in args.workers_order.split(","))
    run_benchmark(args.catalog_size, args.chunk_size, workers_order)


if __name__ == "__main__":
    main()
