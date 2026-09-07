from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix


@dataclass(frozen=True, slots=True)
class UserRepresentationComponentMeans:
    semantic_bias: float
    semantic_embedding: np.ndarray
    identity_bias: float
    identity_embedding: np.ndarray


def mean_known_user_representation(model, user_features, *, block_size: int = 10_000):
    if user_features.shape[0] <= 0:
        raise ValueError("score centering requires at least one known user")
    bias_sum = 0.0
    embedding_sum = None
    count = 0
    for start in range(0, user_features.shape[0], block_size):
        end = min(start + block_size, user_features.shape[0])
        biases, embeddings = model.get_user_representations(user_features[start:end])
        bias_sum += float(np.sum(biases, dtype=np.float64))
        block_sum = np.sum(embeddings, axis=0, dtype=np.float64)
        embedding_sum = block_sum if embedding_sum is None else embedding_sum + block_sum
        count += end - start
    return bias_sum / count, (embedding_sum / count).astype(np.float32)


def center_known_user_representations(
    user_biases,
    user_embeddings,
    *,
    mean_user_bias: float,
    mean_user_embedding,
    weight: float,
):
    if not 0.0 <= weight <= 1.0:
        raise ValueError("known-user score centering weight must be in [0, 1]")
    return (
        np.asarray(user_biases, dtype=np.float32) - weight * mean_user_bias,
        np.asarray(user_embeddings, dtype=np.float32) - weight * mean_user_embedding,
    )


def mean_user_representation_components(
    model,
    user_features,
    *,
    identity_feature_count: int,
) -> UserRepresentationComponentMeans:
    if user_features.shape[0] <= 0:
        raise ValueError("component centering requires at least one known user")
    full_biases, full_embeddings = model.get_user_representations(user_features)
    semantic_features = without_user_identity_features(
        user_features,
        identity_feature_count=identity_feature_count,
    )
    semantic_biases, semantic_embeddings = model.get_user_representations(
        semantic_features
    )
    full_biases = np.asarray(full_biases, dtype=np.float32)
    full_embeddings = np.asarray(full_embeddings, dtype=np.float32)
    semantic_biases = np.asarray(semantic_biases, dtype=np.float32)
    semantic_embeddings = np.asarray(semantic_embeddings, dtype=np.float32)
    return UserRepresentationComponentMeans(
        semantic_bias=float(np.mean(semantic_biases, dtype=np.float64)),
        semantic_embedding=np.mean(
            semantic_embeddings,
            axis=0,
            dtype=np.float64,
        ).astype(np.float32),
        identity_bias=float(
            np.mean(full_biases - semantic_biases, dtype=np.float64)
        ),
        identity_embedding=np.mean(
            full_embeddings - semantic_embeddings,
            axis=0,
            dtype=np.float64,
        ).astype(np.float32),
    )


def collaborative_adjusted_user_representations(
    model,
    user_features,
    *,
    identity_feature_count: int,
    collaborative_confidences,
    centering_weight: float,
    component_means: UserRepresentationComponentMeans | None,
):
    if not 0.0 <= centering_weight <= 1.0:
        raise ValueError("known-user score centering weight must be in [0, 1]")
    row_count = int(user_features.shape[0])
    confidences = np.asarray(collaborative_confidences, dtype=np.float32).reshape(-1)
    if confidences.shape != (row_count,) or not np.all(np.isfinite(confidences)):
        raise ValueError("collaborative confidences must align with user feature rows")
    if np.any(confidences < 0.0) or np.any(confidences > 1.0):
        raise ValueError("collaborative confidences must be in [0, 1]")
    if centering_weight > 0 and component_means is None:
        raise ValueError("component means are required when score centering is enabled")

    full_biases, full_embeddings = model.get_user_representations(user_features)
    semantic_features = without_user_identity_features(
        user_features,
        identity_feature_count=identity_feature_count,
    )
    semantic_biases, semantic_embeddings = model.get_user_representations(
        semantic_features
    )
    full_biases = np.asarray(full_biases, dtype=np.float32)
    full_embeddings = np.asarray(full_embeddings, dtype=np.float32)
    semantic_biases = np.asarray(semantic_biases, dtype=np.float32)
    semantic_embeddings = np.asarray(semantic_embeddings, dtype=np.float32)
    identity_biases = full_biases - semantic_biases
    identity_embeddings = full_embeddings - semantic_embeddings

    if component_means is not None and centering_weight > 0:
        semantic_biases = (
            semantic_biases - centering_weight * component_means.semantic_bias
        )
        semantic_embeddings = (
            semantic_embeddings
            - centering_weight * component_means.semantic_embedding
        )
        identity_biases = (
            identity_biases - centering_weight * component_means.identity_bias
        )
        identity_embeddings = (
            identity_embeddings
            - centering_weight * component_means.identity_embedding
        )

    return (
        semantic_biases + confidences * identity_biases,
        semantic_embeddings + confidences[:, np.newaxis] * identity_embeddings,
    )


def without_user_identity_features(
    user_features,
    *,
    identity_feature_count: int,
) -> csr_matrix:
    if identity_feature_count < 0 or identity_feature_count > user_features.shape[1]:
        raise ValueError("user identity feature count is outside the feature matrix")
    matrix = user_features.tocoo(copy=False)
    keep = matrix.col >= identity_feature_count
    return csr_matrix(
        (
            matrix.data[keep].astype(np.float32, copy=False),
            (matrix.row[keep], matrix.col[keep]),
        ),
        shape=matrix.shape,
        dtype=np.float32,
    )
