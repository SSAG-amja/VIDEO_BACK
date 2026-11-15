from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

import numpy as np
from scipy.sparse import csr_matrix

from app.services.recsys.v3.config import CANDIDATE_ITEM_BLOCK_SIZE
from app.services.recsys.v3.domain.feature_registry import get_feature_definition
from app.services.recsys.v3.domain.schemas import ProfileFeatureSignal
from app.services.recsys.v3.serving.model_store import RuntimeHybridArtifact


FIELD_BUDGETED_POLICY = "supported_identity_field_budgeted"
KEYWORD_WEIGHTING_POLICY = "normalized_idf"


def supports_budgeted_ontology_retrieval(artifact: RuntimeHybridArtifact) -> bool:
    feature_exports = artifact.manifest.get("feature_exports", {})
    return (
        artifact.ontology_build_id > 0
        and feature_exports.get("item_representation_policy") == FIELD_BUDGETED_POLICY
        and feature_exports.get("item_keyword_weighting_policy")
        == KEYWORD_WEIGHTING_POLICY
        and bool(feature_exports.get("item_semantic_field_budgets"))
    )


def retrieve_budgeted_ontology_rows(
    artifact: RuntimeHybridArtifact,
    *,
    features: Sequence[ProfileFeatureSignal],
    excluded_movie_ids: Collection[int],
    limit: int,
) -> list[tuple[int, float]]:
    if limit <= 0:
        raise ValueError("ontology feature retrieval limit must be positive")
    if not supports_budgeted_ontology_retrieval(artifact):
        raise ValueError("artifact does not support field-budgeted ontology retrieval")
    profile_row = build_ontology_profile_row(artifact, features)
    if profile_row.nnz == 0:
        return []

    excluded = {int(movie_id) for movie_id in excluded_movie_ids}
    top_movie_ids = np.empty(0, dtype=np.int64)
    top_scores = np.empty(0, dtype=np.float32)
    for start in range(0, len(artifact.movie_ids), CANDIDATE_ITEM_BLOCK_SIZE):
        end = min(start + CANDIDATE_ITEM_BLOCK_SIZE, len(artifact.movie_ids))
        scores = np.asarray(
            artifact.item_features[start:end].dot(profile_row.transpose()).toarray(),
            dtype=np.float32,
        ).reshape(-1)
        movie_ids = np.asarray(artifact.movie_ids[start:end], dtype=np.int64)
        invalid = scores <= 0.0
        if excluded:
            invalid |= np.fromiter(
                (int(movie_id) in excluded for movie_id in movie_ids),
                dtype=np.bool_,
                count=movie_ids.size,
            )
        scores[invalid] = -np.inf
        selected = _exact_top_k_indices(scores, movie_ids, limit)
        if selected.size:
            merged_movie_ids = np.concatenate((top_movie_ids, movie_ids[selected]))
            merged_scores = np.concatenate((top_scores, scores[selected]))
            keep = _exact_top_k_indices(merged_scores, merged_movie_ids, limit)
            top_movie_ids = merged_movie_ids[keep]
            top_scores = merged_scores[keep].astype(np.float32, copy=False)
    return [
        (int(movie_id), float(score))
        for movie_id, score in zip(top_movie_ids, top_scores, strict=True)
    ]


def build_ontology_profile_row(
    artifact: RuntimeHybridArtifact,
    features: Sequence[ProfileFeatureSignal],
) -> csr_matrix:
    peaks: dict[str, float] = defaultdict(float)
    for signal in features:
        peaks[signal.feature.value] = max(peaks[signal.feature.value], float(signal.score))

    values: dict[int, float] = {}
    for signal in features:
        peak = peaks[signal.feature.value]
        if peak <= 0.0:
            continue
        token = get_feature_definition(signal.feature).token(signal.ref_id)
        feature_index = artifact.item_semantic_feature_index.get(token)
        if feature_index is None:
            continue
        values[feature_index] = max(
            values.get(feature_index, 0.0),
            min(float(signal.score) / peak, 1.0),
        )
    if not values:
        return csr_matrix((1, artifact.item_features.shape[1]), dtype=np.float32)
    ordered = sorted(values.items())
    return csr_matrix(
        (
            np.asarray([value for _index, value in ordered], dtype=np.float32),
            np.asarray([index for index, _value in ordered], dtype=np.int32),
            np.asarray([0, len(ordered)], dtype=np.int32),
        ),
        shape=(1, artifact.item_features.shape[1]),
        dtype=np.float32,
    )


def build_budgeted_candidate_aggregate_rows(
    artifact: RuntimeHybridArtifact,
    *,
    candidate_movie_ids: Sequence[int],
    profile_rows: Sequence[tuple[str, str, str, str, str, str, float]],
) -> list[tuple[int, str, str, str, float, float, int]]:
    """Calculate bounded ontology matches for an already-bounded candidate set."""
    if not supports_budgeted_ontology_retrieval(artifact):
        raise ValueError("artifact does not support field-budgeted ontology analysis")

    grouped: dict[tuple[str, str, str], dict[int, float]] = defaultdict(dict)
    for _relation, feature, _node_type, ref_id, scope, direction, score in profile_rows:
        feature_index = artifact.item_semantic_feature_index.get(f"{feature}:{ref_id}")
        if feature_index is None:
            continue
        key = (feature, scope, direction)
        grouped[key][feature_index] = max(
            grouped[key].get(feature_index, 0.0),
            float(score),
        )

    indexed_movies = [
        (int(movie_id), artifact.movie_index(int(movie_id)))
        for movie_id in candidate_movie_ids
    ]
    indexed_movies = [
        (movie_id, row_index)
        for movie_id, row_index in indexed_movies
        if row_index is not None
    ]
    if not indexed_movies or not grouped:
        return []

    movie_ids = np.asarray([movie_id for movie_id, _row in indexed_movies], dtype=np.int64)
    movie_rows = np.asarray([row for _movie_id, row in indexed_movies], dtype=np.int64)
    result: list[tuple[int, str, str, str, float, float, int]] = []
    for (feature, scope, direction), profile_values in sorted(grouped.items()):
        peak = max(profile_values.values(), default=0.0)
        if peak <= 0.0:
            continue
        columns = np.asarray(sorted(profile_values), dtype=np.int64)
        weights = np.asarray(
            [min(profile_values[column] / peak, 1.0) for column in columns],
            dtype=np.float32,
        )
        matching = artifact.item_features[movie_rows][:, columns]
        scores = np.asarray(matching.dot(weights), dtype=np.float32).reshape(-1)
        counts = np.asarray(matching.getnnz(axis=1), dtype=np.int32).reshape(-1)
        for movie_id, score, match_count in zip(
            movie_ids,
            scores,
            counts,
            strict=True,
        ):
            if score <= 0.0:
                continue
            result.append(
                (
                    int(movie_id),
                    feature,
                    scope,
                    direction,
                    float(score),
                    float(score),
                    int(match_count),
                )
            )
    return sorted(result, key=lambda row: (row[0], row[1], row[2], row[3]))


def _exact_top_k_indices(
    scores: np.ndarray,
    movie_ids: np.ndarray,
    top_k: int,
) -> np.ndarray:
    valid = np.flatnonzero(np.isfinite(scores))
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
