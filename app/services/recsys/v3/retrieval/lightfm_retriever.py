from __future__ import annotations

import hashlib
import json
from collections.abc import Collection

import numpy as np
from scipy.sparse import csr_matrix

from app.services.recsys.v3.config import (
    CANDIDATE_ITEM_BLOCK_SIZE,
    CANDIDATE_STORAGE_SIZE,
    USER_FEATURE_EXPLICIT_GENRE_WEIGHT,
    USER_FEATURE_FAVORITE_DERIVED_WEIGHT,
)
from app.services.recsys.v3.domain.feature_registry import FeatureName, get_feature_definition
from app.services.recsys.v3.serving.model_store import RuntimeHybridArtifact
from app.services.recsys.v3.retrieval.retrieval_schemas import LongTermCandidate
from app.services.recsys.v3.domain.schemas import UserProfileBundle


def retrieve_lightfm_candidates(
    artifact: RuntimeHybridArtifact,
    *,
    profile: UserProfileBundle,
    excluded_movie_ids: Collection[int],
    limit: int = CANDIDATE_STORAGE_SIZE,
    force_feature_only: bool = False,
) -> tuple[LongTermCandidate, ...]:
    if limit <= 0 or limit > CANDIDATE_STORAGE_SIZE:
        raise ValueError(f"LightFM retrieval limit must be between 1 and {CANDIDATE_STORAGE_SIZE}")
    user_index = artifact.user_index(profile.user_id)
    if user_index is not None and not force_feature_only:
        user_features = artifact.user_features[user_index]
    else:
        user_features = build_feature_only_user_row(artifact, profile)
        if user_features.nnz == 0:
            return ()

    user_biases, user_embeddings = artifact.model.get_user_representations(user_features)
    user_bias = float(np.asarray(user_biases).reshape(-1)[0])
    user_embedding = np.asarray(user_embeddings, dtype=np.float32).reshape(1, -1)
    top_movie_ids = np.empty(0, dtype=np.int64)
    top_scores = np.empty(0, dtype=np.float32)
    excluded = set(int(value) for value in excluded_movie_ids)
    for start in range(0, len(artifact.movie_ids), CANDIDATE_ITEM_BLOCK_SIZE):
        end = min(start + CANDIDATE_ITEM_BLOCK_SIZE, len(artifact.movie_ids))
        item_features = artifact.item_features[start:end]
        item_biases, item_embeddings = artifact.model.get_item_representations(item_features)
        scores = np.asarray(item_embeddings, dtype=np.float32) @ user_embedding[0]
        scores += np.asarray(item_biases, dtype=np.float32)
        scores += user_bias
        movie_ids = np.asarray(artifact.movie_ids[start:end], dtype=np.int64)
        if excluded:
            mask = np.fromiter(
                (int(movie_id) in excluded for movie_id in movie_ids),
                dtype=np.bool_,
                count=movie_ids.size,
            )
            scores[mask] = -np.inf
        selected = _exact_top_k_indices(scores, movie_ids, limit)
        if selected.size:
            merged_movie_ids = np.concatenate((top_movie_ids, movie_ids[selected]))
            merged_scores = np.concatenate((top_scores, scores[selected]))
            keep = _exact_top_k_indices(merged_scores, merged_movie_ids, limit)
            top_movie_ids = merged_movie_ids[keep]
            top_scores = merged_scores[keep].astype(np.float32, copy=False)
    return tuple(
        LongTermCandidate(
            movie_id=int(movie_id),
            model_raw_score=float(score),
            source_rank=rank,
        )
        for rank, (movie_id, score) in enumerate(
            zip(top_movie_ids, top_scores, strict=True),
            start=1,
        )
    )


def build_feature_only_user_row(
    artifact: RuntimeHybridArtifact,
    profile: UserProfileBundle,
) -> csr_matrix:
    token_map = {token: index for index, token in enumerate(artifact.user_feature_tokens)}
    values: dict[int, float] = {}
    genre_definition = get_feature_definition(FeatureName.GENRE)
    for genre_id in profile.onboarding.genre_ids:
        index = token_map.get(genre_definition.token(genre_id))
        if index is not None:
            values[index] = USER_FEATURE_EXPLICIT_GENRE_WEIGHT

    movie_identity_count = len(artifact.movie_ids)
    for movie_id in profile.onboarding.favorite_movie_ids:
        movie_index = artifact.movie_index(movie_id)
        if movie_index is None:
            continue
        row = artifact.item_features[movie_index]
        for item_index, item_value in zip(row.indices, row.data, strict=True):
            if int(item_index) < movie_identity_count:
                continue
            token = artifact.item_feature_tokens[int(item_index)]
            user_index = token_map.get(token)
            if user_index is None:
                continue
            value = USER_FEATURE_FAVORITE_DERIVED_WEIGHT * float(item_value)
            values[user_index] = max(values.get(user_index, 0.0), value)
    if not values:
        return csr_matrix((1, len(artifact.user_feature_tokens)), dtype=np.float32)
    ordered = sorted(values.items())
    return csr_matrix(
        (
            np.asarray([value for _index, value in ordered], dtype=np.float32),
            np.asarray([index for index, _value in ordered], dtype=np.int32),
            np.asarray([0, len(ordered)], dtype=np.int32),
        ),
        shape=(1, len(artifact.user_feature_tokens)),
        dtype=np.float32,
    )


def onboarding_profile_signature(profile: UserProfileBundle) -> str:
    payload = {
        "favorite_movie_ids": sorted(profile.onboarding.favorite_movie_ids),
        "genre_ids": sorted(profile.onboarding.genre_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def onboarding_features_changed(
    artifact: RuntimeHybridArtifact,
    profile: UserProfileBundle,
) -> bool:
    user_index = artifact.user_index(profile.user_id)
    if user_index is None:
        return True
    current = build_feature_only_user_row(artifact, profile)
    stored = artifact.user_features[user_index]
    identity_prefix = f"{get_feature_definition(FeatureName.USER_IDENTITY).namespace}:"
    shared_indices = np.asarray(
        [
            index
            for index, token in enumerate(artifact.user_feature_tokens)
            if not token.startswith(identity_prefix)
        ],
        dtype=np.int32,
    )
    if shared_indices.size == 0:
        return False
    difference = stored[:, shared_indices] - current[:, shared_indices]
    return bool(difference.nnz and np.any(np.abs(difference.data) > 1e-6))


def _exact_top_k_indices(scores: np.ndarray, movie_ids: np.ndarray, top_k: int) -> np.ndarray:
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
