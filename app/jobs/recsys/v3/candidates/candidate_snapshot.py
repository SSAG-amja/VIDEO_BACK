from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import numpy as np

from app.jobs.recsys.v3.candidates.candidate_materializer import materialize_candidate_batch
from app.jobs.recsys.v3.candidates.candidate_schemas import (
    CandidateBatch,
    CandidateFailure,
    CandidateMaterializationConfig,
    LoadedCandidateSnapshot,
)
from app.jobs.recsys.v3.training.model_schemas import LoadedHybridArtifact
from app.services.recsys.v3.config import CANDIDATE_SNAPSHOT_FORMAT_VERSION, ENGINE_NAME, ENGINE_VERSION


def materialize_candidate_snapshot(
    artifact: LoadedHybridArtifact,
    *,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]] | None = None,
    eligible_user_ids: Sequence[int] | None = None,
    config: CandidateMaterializationConfig | None = None,
    output_root: str | Path = "assets/ml_models/v3/candidate_snapshots",
) -> LoadedCandidateSnapshot:
    materialization_config = config or CandidateMaterializationConfig()
    exclusions = exclusions_by_user_id or {}
    user_indices = _resolve_user_indices(artifact, eligible_user_ids)
    if user_indices.size == 0:
        raise ValueError("candidate materialization requires at least one eligible artifact user")

    model_build_id = str(artifact.manifest["model_build_id"])
    exclusion_hash = hash_exclusions(exclusions, artifact.user_ids[user_indices])
    input_payload = {
        "snapshot_format_version": CANDIDATE_SNAPSHOT_FORMAT_VERSION,
        "model_build_id": model_build_id,
        "model_manifest_hash": _hash_json(artifact.manifest),
        "config": materialization_config.result_config,
        "config_hash": materialization_config.config_hash,
        "exclusion_hash": exclusion_hash,
        "eligible_user_ids_hash": hash_eligible_user_ids(artifact.user_ids[user_indices]),
        "eligible_user_count": int(user_indices.size),
    }
    snapshot_digest = _hash_json(input_payload)
    snapshot_id = f"cand-{snapshot_digest[:24]}"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / snapshot_id
    if target.exists():
        loaded = load_candidate_snapshot(target)
        if loaded.manifest["input_hash"] != snapshot_digest:
            raise ValueError("existing candidate snapshot has a conflicting input hash")
        return loaded

    work_dir = root / f".{snapshot_id}.inprogress"
    lock_path = root / f".{snapshot_id}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(f"candidate materialization is already running: {snapshot_id}") from exc

    wall_started = time.perf_counter()
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        batch_dir = work_dir / "batches"
        batch_dir.mkdir(exist_ok=True)
        input_path = work_dir / "input.json"
        if input_path.exists():
            if _read_json(input_path) != input_payload:
                raise ValueError("candidate checkpoint inputs do not match the requested run")
        else:
            _write_json_atomic(input_path, input_payload)

        for start in range(0, user_indices.size, materialization_config.checkpoint_user_count):
            end = min(start + materialization_config.checkpoint_user_count, user_indices.size)
            stem = f"batch-{start:09d}-{end:09d}"
            npz_path = batch_dir / f"{stem}.npz"
            metadata_path = batch_dir / f"{stem}.json"
            if npz_path.exists() and metadata_path.exists():
                _validate_batch_files(npz_path, metadata_path, artifact.movie_ids, materialization_config.top_k)
                continue

            batch = materialize_candidate_batch(
                artifact,
                user_indices[start:end],
                exclusions_by_user_id=exclusions,
                config=materialization_config,
            )
            _write_batch_atomic(npz_path, metadata_path, batch)
            _validate_batch_files(npz_path, metadata_path, artifact.movie_ids, materialization_config.top_k)
            _write_progress(work_dir, batch_dir, user_indices.size)

        batch_files = sorted(batch_dir.glob("batch-*.npz"))
        aggregate = _aggregate_batch_diagnostics(batch_files, materialization_config.top_k)
        if aggregate["successful_user_count"] == 0:
            raise RuntimeError("candidate materialization failed for every eligible user")
        manifest = {
            "snapshot_format_version": CANDIDATE_SNAPSHOT_FORMAT_VERSION,
            "candidate_snapshot_id": snapshot_id,
            "input_hash": snapshot_digest,
            "engine_name": ENGINE_NAME,
            "engine_version": ENGINE_VERSION,
            "model_build_id": model_build_id,
            "ontology_build_id": artifact.manifest.get("ontology", {}).get("build_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config": materialization_config.result_config,
            "execution_config": materialization_config.execution_config,
            "config_hash": materialization_config.config_hash,
            "exclusion_hash": exclusion_hash,
            "eligible_user_ids_hash": input_payload["eligible_user_ids_hash"],
            "eligible_user_count": int(user_indices.size),
            "successful_user_count": aggregate["successful_user_count"],
            "failed_user_count": aggregate["failed_user_count"],
            "candidate_count": aggregate["candidate_count"],
            "score_seconds": aggregate["score_seconds"],
            "wall_seconds": time.perf_counter() - wall_started,
            "seconds_per_successful_user": (
                aggregate["score_seconds"] / aggregate["successful_user_count"]
            ),
            "peak_score_block_bytes": aggregate["peak_score_block_bytes"],
            "batch_count": len(batch_files),
            "files": {
                str(path.relative_to(work_dir)): {
                    "sha256": _hash_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(batch_dir.iterdir())
                if path.is_file()
            },
        }
        _write_json_atomic(work_dir / "manifest.json", manifest)
        load_candidate_snapshot(work_dir)
        work_dir.rename(target)
        return load_candidate_snapshot(target)
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


def load_candidate_snapshot(path: str | Path) -> LoadedCandidateSnapshot:
    snapshot_path = Path(path)
    manifest = _read_json(snapshot_path / "manifest.json")
    if manifest.get("snapshot_format_version") != CANDIDATE_SNAPSHOT_FORMAT_VERSION:
        raise ValueError("unsupported candidate snapshot format")
    expected_id = f"cand-{str(manifest.get('input_hash', ''))[:24]}"
    if manifest.get("candidate_snapshot_id") != expected_id:
        raise ValueError("candidate snapshot ID does not match its input hash")
    for relative, expected in manifest.get("files", {}).items():
        file_path = snapshot_path / relative
        if not file_path.is_file() or file_path.stat().st_size != expected["size_bytes"]:
            raise ValueError(f"candidate snapshot file is missing or changed: {relative}")
        if _hash_file(file_path) != expected["sha256"]:
            raise ValueError(f"candidate snapshot file hash mismatch: {relative}")

    batches = list(iter_candidate_snapshot_batches(LoadedCandidateSnapshot(snapshot_path, manifest)))
    successful = sum(batch.successful_user_ids.size for batch in batches)
    failed = sum(len(batch.failures) for batch in batches)
    candidates = sum(batch.candidate_count for batch in batches)
    if successful != manifest["successful_user_count"]:
        raise ValueError("candidate snapshot successful user count mismatch")
    if failed != manifest["failed_user_count"] or candidates != manifest["candidate_count"]:
        raise ValueError("candidate snapshot aggregate count mismatch")
    return LoadedCandidateSnapshot(path=snapshot_path, manifest=manifest)


def iter_candidate_snapshot_batches(snapshot: LoadedCandidateSnapshot) -> Iterator[CandidateBatch]:
    top_k = int(snapshot.manifest["config"]["top_k"])
    for npz_path in sorted((snapshot.path / "batches").glob("batch-*.npz")):
        metadata_path = npz_path.with_suffix(".json")
        yield _load_batch(npz_path, metadata_path, None, top_k)


def hash_exclusions(
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]],
    eligible_user_ids: Sequence[int],
) -> str:
    digest = hashlib.sha256(b"v3-candidate-exclusions-v1\0")
    for user_id in np.asarray(eligible_user_ids, dtype=np.int64):
        digest.update(f"{int(user_id)}:".encode())
        for movie_id in sorted(exclusions_by_user_id.get(int(user_id), ())):
            digest.update(f"{int(movie_id)},".encode())
        digest.update(b";")
    return digest.hexdigest()


def hash_eligible_user_ids(eligible_user_ids: Sequence[int]) -> str:
    return _hash_int_array(eligible_user_ids)


def _resolve_user_indices(
    artifact: LoadedHybridArtifact,
    eligible_user_ids: Sequence[int] | None,
) -> np.ndarray:
    if eligible_user_ids is None:
        return np.arange(len(artifact.user_ids), dtype=np.int64)
    artifact_user_ids = np.asarray(artifact.user_ids, dtype=np.int64)
    requested = np.asarray(sorted(set(int(value) for value in eligible_user_ids)), dtype=np.int64)
    positions = np.searchsorted(artifact_user_ids, requested)
    if positions.size and (
        np.any(positions >= artifact_user_ids.size)
        or np.any(artifact_user_ids[positions] != requested)
    ):
        raise ValueError("eligible users must be a subset of the artifact user mapping")
    return positions


def _write_batch_atomic(npz_path: Path, metadata_path: Path, batch: CandidateBatch) -> None:
    temporary = npz_path.with_suffix(".npz.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            successful_user_ids=batch.successful_user_ids,
            candidate_user_ids=batch.candidate_user_ids,
            movie_ids=batch.movie_ids,
            model_scores=batch.model_scores,
            source_ranks=batch.source_ranks,
        )
    temporary.replace(npz_path)
    _write_json_atomic(
        metadata_path,
        {
            "failures": [asdict(failure) for failure in batch.failures],
            "elapsed_seconds": batch.elapsed_seconds,
            "peak_score_block_bytes": batch.peak_score_block_bytes,
            "npz_sha256": _hash_file(npz_path),
        },
    )


def _load_batch(
    npz_path: Path,
    metadata_path: Path,
    artifact_movie_ids: np.ndarray | None,
    top_k: int,
) -> CandidateBatch:
    metadata = _read_json(metadata_path)
    if metadata.get("npz_sha256") != _hash_file(npz_path):
        raise ValueError(f"candidate batch data hash mismatch: {npz_path.name}")
    with np.load(npz_path, allow_pickle=False) as data:
        batch = CandidateBatch(
            successful_user_ids=data["successful_user_ids"].astype(np.int64, copy=False),
            candidate_user_ids=data["candidate_user_ids"].astype(np.int64, copy=False),
            movie_ids=data["movie_ids"].astype(np.int64, copy=False),
            model_scores=data["model_scores"].astype(np.float32, copy=False),
            source_ranks=data["source_ranks"].astype(np.int32, copy=False),
            failures=tuple(CandidateFailure(**item) for item in metadata.get("failures", ())),
            elapsed_seconds=float(metadata["elapsed_seconds"]),
            peak_score_block_bytes=int(metadata["peak_score_block_bytes"]),
        )
    _validate_batch(batch, artifact_movie_ids, top_k)
    return batch


def _validate_batch_files(
    npz_path: Path,
    metadata_path: Path,
    artifact_movie_ids: np.ndarray,
    top_k: int,
) -> None:
    _load_batch(npz_path, metadata_path, np.asarray(artifact_movie_ids, dtype=np.int64), top_k)


def _validate_batch(
    batch: CandidateBatch,
    artifact_movie_ids: np.ndarray | None,
    top_k: int,
) -> None:
    size = batch.candidate_count
    if not (
        batch.candidate_user_ids.size
        == batch.model_scores.size
        == batch.source_ranks.size
        == size
    ):
        raise ValueError("candidate batch arrays are not aligned")
    if not np.all(np.isfinite(batch.model_scores)):
        raise ValueError("candidate batch contains non-finite model scores")
    if np.unique(batch.successful_user_ids).size != batch.successful_user_ids.size:
        raise ValueError("candidate batch contains duplicate successful users")
    if set(batch.successful_user_ids).intersection(failure.user_id for failure in batch.failures):
        raise ValueError("candidate batch marks a user as both successful and failed")
    if artifact_movie_ids is not None and batch.movie_ids.size:
        positions = np.searchsorted(artifact_movie_ids, batch.movie_ids)
        if np.any(positions >= artifact_movie_ids.size) or np.any(artifact_movie_ids[positions] != batch.movie_ids):
            raise ValueError("candidate batch contains a movie outside the artifact mapping")
    for user_id in batch.successful_user_ids:
        mask = batch.candidate_user_ids == user_id
        ranks = batch.source_ranks[mask]
        if ranks.size > top_k or not np.array_equal(ranks, np.arange(1, ranks.size + 1)):
            raise ValueError("candidate source ranks must be contiguous and bounded")
        if np.unique(batch.movie_ids[mask]).size != ranks.size:
            raise ValueError("candidate batch contains duplicate movies for a user")
        scores = batch.model_scores[mask]
        movies = batch.movie_ids[mask]
        expected = np.lexsort((movies, -scores))
        if not np.array_equal(expected, np.arange(ranks.size)):
            raise ValueError("candidate rows are not deterministically score ordered")


def _aggregate_batch_diagnostics(
    batch_files: Sequence[Path],
    top_k: int,
) -> dict[str, int | float]:
    successful = failed = candidates = peak = 0
    score_seconds = 0.0
    for npz_path in batch_files:
        batch = _load_batch(npz_path, npz_path.with_suffix(".json"), None, top_k)
        successful += int(batch.successful_user_ids.size)
        failed += len(batch.failures)
        candidates += batch.candidate_count
        score_seconds += batch.elapsed_seconds
        peak = max(peak, batch.peak_score_block_bytes)
    return {
        "successful_user_count": successful,
        "failed_user_count": failed,
        "candidate_count": candidates,
        "score_seconds": score_seconds,
        "peak_score_block_bytes": peak,
    }


def _write_progress(work_dir: Path, batch_dir: Path, eligible_user_count: int) -> None:
    metadata_files = sorted(batch_dir.glob("batch-*.json"))
    _write_json_atomic(
        work_dir / "progress.json",
        {
            "eligible_user_count": eligible_user_count,
            "completed_checkpoint_count": len(metadata_files),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _hash_int_array(values: Sequence[int]) -> str:
    array = np.asarray(values, dtype="<i8")
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
