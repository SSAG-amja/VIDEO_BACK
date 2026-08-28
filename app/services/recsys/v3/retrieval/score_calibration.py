from __future__ import annotations

import numpy as np


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
