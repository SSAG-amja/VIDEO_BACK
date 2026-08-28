from __future__ import annotations

import queue
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.jobs.recsys.v3.candidates.candidate_schemas import (
    CandidateBatch,
    CandidateFailure,
    CandidateMaterializationConfig,
)
from app.jobs.recsys.v3.training.model_schemas import LoadedHybridArtifact
from app.services.recsys.v3.retrieval.score_calibration import (
    center_known_user_representations,
    mean_known_user_representation,
)


def materialize_candidate_batch(
    artifact: LoadedHybridArtifact,
    user_indices: Sequence[int],
    *,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]] | None = None,
    config: CandidateMaterializationConfig | None = None,
) -> CandidateBatch:
    materialization_config = config or CandidateMaterializationConfig()
    indices = np.asarray(user_indices, dtype=np.int64)
    _validate_artifact(artifact)
    _validate_user_indices(indices, len(artifact.user_ids))
    exclusions = exclusions_by_user_id or {}
    centering_weight = artifact.config.known_user_score_centering_weight
    mean_user_representation = (
        mean_known_user_representation(artifact.model, artifact.user_features)
        if centering_weight > 0
        else None
    )

    started = time.perf_counter()
    blocks = [
        (
            ordinal,
            indices[start : start + materialization_config.user_block_size],
        )
        for ordinal, start in enumerate(
            range(0, indices.size, materialization_config.user_block_size)
        )
    ]
    if materialization_config.worker_count == 1 or len(blocks) <= 1:
        ordered_batches = [
            (
                ordinal,
                _score_block_with_fallback(
                    artifact,
                    block_indices,
                    exclusions_by_user_id=exclusions,
                    config=materialization_config,
                    centering_weight=centering_weight,
                    mean_user_representation=mean_user_representation,
                ),
            )
            for ordinal, block_indices in blocks
        ]
        active_workers = 1
    else:
        ordered_batches = _score_dynamic_blocks(
            artifact,
            blocks,
            exclusions_by_user_id=exclusions,
            config=materialization_config,
            centering_weight=centering_weight,
            mean_user_representation=mean_user_representation,
        )
        active_workers = min(materialization_config.worker_count, len(blocks))

    ordered_batches.sort(key=lambda value: value[0])
    batches = [
        batch
        for _ordinal, block_batches in ordered_batches
        for batch in block_batches
    ]
    block_peaks = sorted(
        (
            max((batch.peak_score_block_bytes for batch in block_batches), default=0)
            for _ordinal, block_batches in ordered_batches
        ),
        reverse=True,
    )
    estimated_concurrent_peak = sum(block_peaks[:active_workers])
    return _merge_batches(
        batches,
        time.perf_counter() - started,
        peak_score_block_bytes=estimated_concurrent_peak,
    )


def _score_dynamic_blocks(
    artifact: LoadedHybridArtifact,
    blocks: Sequence[tuple[int, np.ndarray]],
    *,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]],
    config: CandidateMaterializationConfig,
    centering_weight: float,
    mean_user_representation,
) -> list[tuple[int, list[CandidateBatch]]]:
    work_queue: queue.Queue[tuple[int, np.ndarray]] = queue.Queue()
    for block in blocks:
        work_queue.put(block)

    def worker() -> list[tuple[int, list[CandidateBatch]]]:
        completed: list[tuple[int, list[CandidateBatch]]] = []
        while True:
            try:
                ordinal, block_indices = work_queue.get_nowait()
            except queue.Empty:
                break
            try:
                completed.append(
                    (
                        ordinal,
                        _score_block_with_fallback(
                            artifact,
                            block_indices,
                            exclusions_by_user_id=exclusions_by_user_id,
                            config=config,
                            centering_weight=centering_weight,
                            mean_user_representation=mean_user_representation,
                        ),
                    )
                )
            finally:
                work_queue.task_done()
        return completed

    worker_count = min(config.worker_count, len(blocks))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="v3-candidate",
    ) as executor:
        futures = [executor.submit(worker) for _ in range(worker_count)]
        return [item for future in futures for item in future.result()]


def _score_block_with_fallback(
    artifact: LoadedHybridArtifact,
    block_indices: np.ndarray,
    *,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]],
    config: CandidateMaterializationConfig,
    centering_weight: float,
    mean_user_representation,
) -> list[CandidateBatch]:
    try:
        return [
            _score_user_block(
                artifact,
                block_indices,
                exclusions_by_user_id=exclusions_by_user_id,
                config=config,
                centering_weight=centering_weight,
                mean_user_representation=mean_user_representation,
            )
        ]
    except Exception as block_error:
        batches: list[CandidateBatch] = []
        for user_index in block_indices:
            try:
                batches.append(
                    _score_user_block(
                        artifact,
                        np.asarray([user_index], dtype=np.int64),
                        exclusions_by_user_id=exclusions_by_user_id,
                        config=config,
                        centering_weight=centering_weight,
                        mean_user_representation=mean_user_representation,
                    )
                )
            except Exception as user_error:
                user_id = int(artifact.user_ids[int(user_index)])
                batches.append(
                    CandidateBatch(
                        successful_user_ids=np.empty(0, dtype=np.int64),
                        candidate_user_ids=np.empty(0, dtype=np.int64),
                        movie_ids=np.empty(0, dtype=np.int64),
                        model_scores=np.empty(0, dtype=np.float32),
                        source_ranks=np.empty(0, dtype=np.int32),
                        failures=(
                            CandidateFailure(
                                user_id=user_id,
                                reason=(
                                    f"{type(user_error).__name__}: {user_error}; "
                                    f"block_error={type(block_error).__name__}"
                                )[:500],
                            ),
                        ),
                        elapsed_seconds=0.0,
                        peak_score_block_bytes=0,
                    )
                )
        return batches


def _score_user_block(
    artifact: LoadedHybridArtifact,
    user_indices: np.ndarray,
    *,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]],
    config: CandidateMaterializationConfig,
    centering_weight: float,
    mean_user_representation,
) -> CandidateBatch:
    started = time.perf_counter()
    user_features = artifact.user_features[user_indices]
    user_biases, user_embeddings = artifact.model.get_user_representations(user_features)
    user_biases = np.asarray(user_biases, dtype=np.float32)
    user_embeddings = np.asarray(user_embeddings, dtype=np.float32)
    if mean_user_representation is not None:
        user_biases, user_embeddings = center_known_user_representations(
            user_biases,
            user_embeddings,
            mean_user_bias=mean_user_representation[0],
            mean_user_embedding=mean_user_representation[1],
            weight=centering_weight,
        )
    if (
        user_biases.shape != (user_indices.size,)
        or user_embeddings.shape[0] != user_indices.size
    ):
        raise ValueError("LightFM returned invalid user representation dimensions")

    top_movies = [np.empty(0, dtype=np.int64) for _ in user_indices]
    top_scores = [np.empty(0, dtype=np.float32) for _ in user_indices]
    exclusion_indices = _map_exclusions_to_item_indices(
        artifact,
        user_indices,
        exclusions_by_user_id,
    )
    peak_score_block_bytes = 0

    for item_start in range(0, len(artifact.movie_ids), config.item_block_size):
        item_end = min(item_start + config.item_block_size, len(artifact.movie_ids))
        item_features = artifact.item_features[item_start:item_end]
        item_biases, item_embeddings = artifact.model.get_item_representations(item_features)
        item_biases = np.asarray(item_biases, dtype=np.float32)
        item_embeddings = np.asarray(item_embeddings, dtype=np.float32)
        if (
            item_biases.shape != (item_end - item_start,)
            or item_embeddings.shape[0] != item_end - item_start
        ):
            raise ValueError("LightFM returned invalid item representation dimensions")

        scores = user_embeddings @ item_embeddings.T
        scores += user_biases[:, np.newaxis]
        scores += (1.0 - centering_weight) * item_biases[np.newaxis, :]
        peak_score_block_bytes = max(peak_score_block_bytes, int(scores.nbytes))
        block_movie_ids = np.asarray(artifact.movie_ids[item_start:item_end], dtype=np.int64)

        for row_index in range(user_indices.size):
            excluded = exclusion_indices[row_index]
            left = int(np.searchsorted(excluded, item_start, side="left"))
            right = int(np.searchsorted(excluded, item_end, side="left"))
            if right > left:
                scores[row_index, excluded[left:right] - item_start] = -np.inf

            selected = _exact_top_k_indices(scores[row_index], block_movie_ids, config.top_k)
            if selected.size == 0:
                continue
            merged_movies = np.concatenate((top_movies[row_index], block_movie_ids[selected]))
            merged_scores = np.concatenate((top_scores[row_index], scores[row_index, selected]))
            keep = _exact_top_k_indices(merged_scores, merged_movies, config.top_k)
            top_movies[row_index] = merged_movies[keep]
            top_scores[row_index] = merged_scores[keep].astype(np.float32, copy=False)

    successful_user_ids: list[int] = []
    candidate_user_ids: list[int] = []
    candidate_movie_ids: list[int] = []
    candidate_scores: list[float] = []
    source_ranks: list[int] = []
    for row_index, user_index in enumerate(user_indices):
        user_id = int(artifact.user_ids[int(user_index)])
        successful_user_ids.append(user_id)
        count = int(top_movies[row_index].size)
        candidate_user_ids.extend([user_id] * count)
        candidate_movie_ids.extend(int(value) for value in top_movies[row_index])
        candidate_scores.extend(float(value) for value in top_scores[row_index])
        source_ranks.extend(range(1, count + 1))

    return CandidateBatch(
        successful_user_ids=np.asarray(successful_user_ids, dtype=np.int64),
        candidate_user_ids=np.asarray(candidate_user_ids, dtype=np.int64),
        movie_ids=np.asarray(candidate_movie_ids, dtype=np.int64),
        model_scores=np.asarray(candidate_scores, dtype=np.float32),
        source_ranks=np.asarray(source_ranks, dtype=np.int32),
        failures=(),
        elapsed_seconds=time.perf_counter() - started,
        peak_score_block_bytes=peak_score_block_bytes,
    )


def _exact_top_k_indices(scores: np.ndarray, movie_ids: np.ndarray, top_k: int) -> np.ndarray:
    valid = np.flatnonzero(np.isfinite(scores))
    if valid.size == 0:
        return valid
    if valid.size > top_k:
        valid_scores = scores[valid]
        partition = np.argpartition(valid_scores, -top_k)[-top_k:]
        threshold = np.min(valid_scores[partition])
        higher = valid[valid_scores > threshold]
        tied = valid[valid_scores == threshold]
        remaining = top_k - higher.size
        tied = tied[np.argsort(movie_ids[tied], kind="stable")[:remaining]]
        valid = np.concatenate((higher, tied))
    order = np.lexsort((movie_ids[valid], -scores[valid]))
    return valid[order]


def _map_exclusions_to_item_indices(
    artifact: LoadedHybridArtifact,
    user_indices: np.ndarray,
    exclusions_by_user_id: Mapping[int, set[int] | frozenset[int]],
) -> list[np.ndarray]:
    movie_ids = np.asarray(artifact.movie_ids, dtype=np.int64)
    result: list[np.ndarray] = []
    for user_index in user_indices:
        user_id = int(artifact.user_ids[int(user_index)])
        excluded_ids = np.asarray(sorted(exclusions_by_user_id.get(user_id, ())), dtype=np.int64)
        if excluded_ids.size == 0:
            result.append(np.empty(0, dtype=np.int64))
            continue
        positions = np.searchsorted(movie_ids, excluded_ids)
        in_bounds = positions < movie_ids.size
        positions = positions[in_bounds]
        excluded_ids = excluded_ids[in_bounds]
        positions = positions[movie_ids[positions] == excluded_ids]
        result.append(np.unique(positions))
    return result


def _merge_batches(
    batches: Sequence[CandidateBatch],
    elapsed_seconds: float,
    *,
    peak_score_block_bytes: int | None = None,
) -> CandidateBatch:
    if not batches:
        return CandidateBatch(
            successful_user_ids=np.empty(0, dtype=np.int64),
            candidate_user_ids=np.empty(0, dtype=np.int64),
            movie_ids=np.empty(0, dtype=np.int64),
            model_scores=np.empty(0, dtype=np.float32),
            source_ranks=np.empty(0, dtype=np.int32),
            failures=(),
            elapsed_seconds=elapsed_seconds,
            peak_score_block_bytes=0,
        )

    def concatenate(name: str, dtype: np.dtype) -> np.ndarray:
        values = [getattr(batch, name) for batch in batches if getattr(batch, name).size]
        return np.concatenate(values).astype(dtype, copy=False) if values else np.empty(0, dtype=dtype)

    return CandidateBatch(
        successful_user_ids=concatenate("successful_user_ids", np.int64),
        candidate_user_ids=concatenate("candidate_user_ids", np.int64),
        movie_ids=concatenate("movie_ids", np.int64),
        model_scores=concatenate("model_scores", np.float32),
        source_ranks=concatenate("source_ranks", np.int32),
        failures=tuple(failure for batch in batches for failure in batch.failures),
        elapsed_seconds=elapsed_seconds,
        peak_score_block_bytes=(
            peak_score_block_bytes
            if peak_score_block_bytes is not None
            else max(batch.peak_score_block_bytes for batch in batches)
        ),
    )


def _validate_artifact(artifact: LoadedHybridArtifact) -> None:
    if artifact.manifest.get("stage") != "hybrid_ontology":
        raise ValueError("candidate materialization requires a hybrid ontology artifact")
    if artifact.user_features.shape[0] != len(artifact.user_ids):
        raise ValueError("artifact user mapping and feature rows differ")
    if artifact.item_features.shape[0] != len(artifact.movie_ids):
        raise ValueError("artifact movie mapping and feature rows differ")
    movie_ids = np.asarray(artifact.movie_ids)
    if movie_ids.size > 1 and np.any(movie_ids[1:] <= movie_ids[:-1]):
        raise ValueError("artifact movie IDs must be strictly increasing")


def _validate_user_indices(user_indices: np.ndarray, user_count: int) -> None:
    if user_indices.ndim != 1:
        raise ValueError("user_indices must be one-dimensional")
    if user_indices.size and (np.min(user_indices) < 0 or np.max(user_indices) >= user_count):
        raise ValueError("user index is outside the artifact mapping")
    if np.unique(user_indices).size != user_indices.size:
        raise ValueError("user_indices cannot contain duplicates")
