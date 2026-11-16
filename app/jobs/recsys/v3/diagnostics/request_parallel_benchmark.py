from __future__ import annotations

import argparse
import hashlib
import json
import queue
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.db.session import SessionLocal
from app.jobs.recsys.v3.diagnostics.candidate_parallel_benchmark import (
    MemorySampler,
    active_artifact_path,
    current_rss_bytes,
)
from app.schemas.recsys import RecommendationMode
from app.services.recsys.v3.recommender import get_recommendations


DEFAULT_OUTPUT_DIR = Path("z_v3_docs/diagnostics")


@dataclass(frozen=True, slots=True)
class RequestResult:
    ordinal: int
    user_id: int
    movie_ids: tuple[int, ...]
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class WorkerMetric:
    worker_id: int
    request_count: int
    active_seconds: float


def run_request(ordinal: int, user_id: int) -> RequestResult:
    started = time.perf_counter()
    with SessionLocal() as db:
        response = get_recommendations(
            db,
            user_id=user_id,
            mode=RecommendationMode.ALL,
            limit=20,
            offset=0,
            shuffle_seed=f"parallel-benchmark-{user_id}",
        )
    return RequestResult(
        ordinal=ordinal,
        user_id=user_id,
        movie_ids=tuple(response.movie_ids),
        elapsed_seconds=time.perf_counter() - started,
    )


def result_hash(results: list[RequestResult]) -> str:
    payload = [
        {"user_id": result.user_id, "movie_ids": list(result.movie_ids)}
        for result in sorted(results, key=lambda value: value.ordinal)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


def run_trial(user_ids: list[int], *, workers: int, trial: int) -> dict[str, object]:
    work_queue: queue.Queue[tuple[int, int]] = queue.Queue()
    for ordinal, user_id in enumerate(user_ids):
        work_queue.put((ordinal, user_id))

    def worker(worker_id: int) -> tuple[list[RequestResult], WorkerMetric]:
        completed: list[RequestResult] = []
        active_seconds = 0.0
        while True:
            try:
                ordinal, user_id = work_queue.get_nowait()
            except queue.Empty:
                break
            try:
                result = run_request(ordinal, user_id)
                completed.append(result)
                active_seconds += result.elapsed_seconds
            finally:
                work_queue.task_done()
        return completed, WorkerMetric(
            worker_id=worker_id,
            request_count=len(completed),
            active_seconds=active_seconds,
        )

    rss_before = current_rss_bytes()
    with MemorySampler() as memory:
        started = time.perf_counter()
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="v3-request-benchmark",
        ) as executor:
            outputs = [
                executor.submit(worker, worker_id)
                for worker_id in range(1, workers + 1)
            ]
            completed = [future.result() for future in outputs]
        elapsed = time.perf_counter() - started

    results = [item for worker_results, _metric in completed for item in worker_results]
    metrics = [metric for _worker_results, metric in completed]
    if len(results) != len(user_ids):
        raise RuntimeError(f"request benchmark completed {len(results)}/{len(user_ids)} users")
    durations = [result.elapsed_seconds for result in results]
    return {
        "trial": trial,
        "workers": workers,
        "scheduler": "shared_dynamic_request_queue" if workers > 1 else "sequential",
        "request_count": len(results),
        "elapsed_seconds": round(elapsed, 6),
        "requests_per_second": round(len(results) / elapsed, 4),
        "mean_request_seconds": round(statistics.mean(durations), 6),
        "p95_request_seconds": round(percentile(durations, 0.95), 6),
        "max_request_seconds": round(max(durations), 6),
        "result_hash": result_hash(results),
        "rss_before_bytes": rss_before,
        "peak_rss_bytes": memory.peak_rss_bytes,
        "rss_growth_bytes": max(0, memory.peak_rss_bytes - rss_before),
        "worker_metrics": [asdict(metric) for metric in metrics],
    }


def summarize(results: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for workers in sorted({int(result["workers"]) for result in results}):
        matching = [result for result in results if int(result["workers"]) == workers]
        summary[str(workers)] = {
            "trials": len(matching),
            "median_elapsed_seconds": round(
                statistics.median(float(result["elapsed_seconds"]) for result in matching),
                6,
            ),
            "median_requests_per_second": round(
                statistics.median(float(result["requests_per_second"]) for result in matching),
                4,
            ),
            "median_mean_request_seconds": round(
                statistics.median(float(result["mean_request_seconds"]) for result in matching),
                6,
            ),
            "max_p95_request_seconds": max(
                float(result["p95_request_seconds"]) for result in matching
            ),
            "max_peak_rss_bytes": max(int(result["peak_rss_bytes"]) for result in matching),
            "max_rss_growth_bytes": max(int(result["rss_growth_bytes"]) for result in matching),
        }
    return summary


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# V3 Request Parallel Benchmark",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- bundle: `{report['bundle_id']}`",
        f"- users: `{report['user_count']}` known users",
        f"- result invariant: `{report['result_invariant']}`",
        "",
        "| workers | trials | batch seconds | requests/sec | mean request | max p95 | peak RSS MiB |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workers, values in report["summary"].items():
        lines.append(
            f"| {workers} | {values['trials']} | {values['median_elapsed_seconds']:.3f} "
            f"| {values['median_requests_per_second']:.3f} "
            f"| {values['median_mean_request_seconds']:.3f} "
            f"| {values['max_p95_request_seconds']:.3f} "
            f"| {values['max_peak_rss_bytes'] / 1024 / 1024:.1f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare V3 online recommendation workers")
    parser.add_argument("--artifact-root", type=Path, default=Path("assets/ml_models/v3"))
    parser.add_argument("--users", type=int, default=12)
    parser.add_argument("--workers-order", default="1,2,4,4,2,1")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workers_order = tuple(int(value) for value in args.workers_order.split(","))
    if args.users <= 0 or not workers_order or any(value <= 0 for value in workers_order):
        raise ValueError("users and worker counts must be positive")

    artifact_path, bundle_manifest = active_artifact_path(args.artifact_root)
    artifact_user_ids = np.load(artifact_path / "user_ids.npy", mmap_mode="r")
    with SessionLocal() as db:
        eligible_user_ids, _exclusions = load_eligible_users_and_exclusions(db, artifact_user_ids)
        db.rollback()
    user_ids = list(eligible_user_ids[: args.users])
    if len(user_ids) < args.users:
        raise RuntimeError(f"requested {args.users} users but only {len(user_ids)} are eligible")

    for ordinal, user_id in enumerate(user_ids):
        run_request(ordinal, user_id)
    results = []
    for trial, workers in enumerate(workers_order, start=1):
        result = run_trial(user_ids, workers=workers, trial=trial)
        print(json.dumps(result, sort_keys=True), flush=True)
        results.append(result)

    hashes = {str(result["result_hash"]) for result in results}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_id": bundle_manifest["bundle_id"],
        "model_build_id": bundle_manifest["model_build_id"],
        "candidate_snapshot_id": bundle_manifest["candidate_snapshot_id"],
        "user_count": len(user_ids),
        "warmup_request_count": len(user_ids),
        "workers_order": list(workers_order),
        "result_invariant": len(hashes) == 1,
        "results": results,
        "summary": summarize(results),
    }
    if not report["result_invariant"]:
        raise RuntimeError("parallel request results differ from the sequential baseline")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_request_parallel_benchmark_{timestamp}.json"
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}), flush=True)


if __name__ == "__main__":
    main()
