from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import threading
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.db.session import SessionLocal
from app.jobs.recsys.v3.candidates.candidate_materializer import materialize_candidate_batch
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateBatch, CandidateMaterializationConfig
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact


DEFAULT_OUTPUT_DIR = Path("z_v3_docs/diagnostics")


class MemorySampler:
    def __init__(self, interval_seconds: float = 0.01) -> None:
        self.interval_seconds = interval_seconds
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, name="v3-memory-sampler")

    def __enter__(self) -> MemorySampler:
        self.peak_rss_bytes = current_rss_bytes()
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        self._thread.join()
        self.peak_rss_bytes = max(self.peak_rss_bytes, current_rss_bytes())

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.peak_rss_bytes = max(self.peak_rss_bytes, current_rss_bytes())


def current_rss_bytes() -> int:
    statm = Path("/proc/self/statm")
    if not statm.exists():
        return 0
    resident_pages = int(statm.read_text(encoding="ascii").split()[1])
    return resident_pages * os.sysconf("SC_PAGE_SIZE")


def active_artifact_path(root: Path) -> tuple[Path, dict]:
    pointer = json.loads((root / "active_bundle.json").read_text(encoding="utf-8"))
    manifest_path = root / str(pointer["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return root / str(manifest["model_artifact_path"]), manifest


def batch_hash(batch: CandidateBatch) -> str:
    digest = hashlib.sha256()
    for values in (
        batch.successful_user_ids,
        batch.candidate_user_ids,
        batch.movie_ids,
        batch.model_scores,
        batch.source_ranks,
    ):
        digest.update(values.tobytes())
    digest.update(
        json.dumps(
            [asdict(failure) for failure in batch.failures],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return digest.hexdigest()


def run_trial(
    *,
    artifact,
    user_indices,
    exclusions,
    base_config: CandidateMaterializationConfig,
    workers: int,
    trial: int,
) -> dict[str, object]:
    config = replace(base_config, worker_count=workers)
    rss_before = current_rss_bytes()
    with MemorySampler() as memory:
        batch = materialize_candidate_batch(
            artifact,
            user_indices,
            exclusions_by_user_id=exclusions,
            config=config,
        )
    if batch.failures:
        raise RuntimeError(f"candidate benchmark trial failed users={len(batch.failures)}")
    elapsed = batch.elapsed_seconds
    return {
        "trial": trial,
        "workers": workers,
        "scheduler": "shared_dynamic_user_block_queue" if workers > 1 else "sequential",
        "elapsed_seconds": round(elapsed, 6),
        "users_per_second": round(len(user_indices) / elapsed, 3),
        "candidate_count": batch.candidate_count,
        "result_hash": batch_hash(batch),
        "rss_before_bytes": rss_before,
        "peak_rss_bytes": memory.peak_rss_bytes,
        "rss_growth_bytes": max(0, memory.peak_rss_bytes - rss_before),
        "estimated_peak_score_bytes": batch.peak_score_block_bytes,
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
            "median_users_per_second": round(
                statistics.median(float(result["users_per_second"]) for result in matching),
                3,
            ),
            "max_peak_rss_bytes": max(int(result["peak_rss_bytes"]) for result in matching),
            "max_rss_growth_bytes": max(int(result["rss_growth_bytes"]) for result in matching),
            "max_estimated_peak_score_bytes": max(
                int(result["estimated_peak_score_bytes"]) for result in matching
            ),
        }
    return summary


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# V3 Candidate Parallel Benchmark",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- model: `{report['model_build_id']}`",
        f"- users: `{report['user_count']}`",
        f"- user block: `{report['config']['user_block_size']}`",
        f"- item block: `{report['config']['item_block_size']}`",
        f"- result invariant: `{report['result_invariant']}`",
        "",
        "| workers | trials | median seconds | users/sec | peak RSS MiB | score block MiB |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workers, values in report["summary"].items():
        lines.append(
            f"| {workers} | {values['trials']} | {values['median_elapsed_seconds']:.3f} "
            f"| {values['median_users_per_second']:.2f} "
            f"| {values['max_peak_rss_bytes'] / 1024 / 1024:.1f} "
            f"| {values['max_estimated_peak_score_bytes'] / 1024 / 1024:.1f} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare V3 candidate materialization workers")
    parser.add_argument("--artifact-root", type=Path, default=Path("assets/ml_models/v3"))
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--workers-order", default="1,2,4,4,2,1")
    parser.add_argument("--user-block-size", type=int, default=None)
    parser.add_argument("--item-block-size", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.users <= 0:
        raise ValueError("benchmark user count must be positive")
    workers_order = tuple(int(value) for value in args.workers_order.split(","))
    if not workers_order or any(value <= 0 for value in workers_order):
        raise ValueError("workers order must contain positive integers")

    artifact_path, bundle_manifest = active_artifact_path(args.artifact_root)
    artifact = load_hybrid_artifact(artifact_path)
    with SessionLocal() as db:
        eligible_user_ids, exclusions = load_eligible_users_and_exclusions(db, artifact.user_ids)
        db.rollback()
    eligible = set(eligible_user_ids)
    user_indices = [
        index
        for index, user_id in enumerate(artifact.user_ids)
        if int(user_id) in eligible
    ][: args.users]
    if len(user_indices) < args.users:
        raise RuntimeError(
            f"requested {args.users} users but only {len(user_indices)} are eligible"
        )

    defaults = CandidateMaterializationConfig()
    base_config = CandidateMaterializationConfig(
        top_k=defaults.top_k,
        user_block_size=args.user_block_size or defaults.user_block_size,
        item_block_size=args.item_block_size or defaults.item_block_size,
        checkpoint_user_count=max(defaults.checkpoint_user_count, args.users),
        worker_count=1,
    )
    materialize_candidate_batch(
        artifact,
        user_indices[:1],
        exclusions_by_user_id=exclusions,
        config=base_config,
    )

    results = []
    for trial, workers in enumerate(workers_order, start=1):
        result = run_trial(
            artifact=artifact,
            user_indices=user_indices,
            exclusions=exclusions,
            base_config=base_config,
            workers=workers,
            trial=trial,
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        results.append(result)

    hashes = {str(result["result_hash"]) for result in results}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_build_id": bundle_manifest["model_build_id"],
        "candidate_snapshot_id": bundle_manifest["candidate_snapshot_id"],
        "user_count": len(user_indices),
        "workers_order": list(workers_order),
        "config": asdict(base_config),
        "result_invariant": len(hashes) == 1,
        "results": results,
        "summary": summarize(results),
    }
    if not report["result_invariant"]:
        raise RuntimeError("parallel candidate results differ from the sequential baseline")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_candidate_parallel_benchmark_{timestamp}.json"
    markdown_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}), flush=True)


if __name__ == "__main__":
    main()
