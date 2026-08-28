from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Literal

import numpy as np
from scipy.sparse import csr_matrix, hstack

from app.jobs.recsys.v3.features.feature_schemas import (
    ItemFeatureExport,
    UserFeatureExport,
)
from app.jobs.recsys.v3.features.user_feature_builder import hash_user_feature_export


FeatureRepresentationPolicy = Literal[
    "full_identity_raw",
    "full_identity_normalized",
    "supported_identity_normalized",
    "metadata_only_normalized",
]

FEATURE_REPRESENTATION_POLICIES: tuple[FeatureRepresentationPolicy, ...] = (
    "full_identity_raw",
    "full_identity_normalized",
    "supported_identity_normalized",
    "metadata_only_normalized",
)


def transform_item_feature_export(
    item_export: ItemFeatureExport,
    *,
    policy: FeatureRepresentationPolicy,
    supported_movie_ids: frozenset[int] = frozenset(),
    identity_weight: float = 1.0,
    semantic_weight: float = 1.0,
) -> ItemFeatureExport:
    _validate_policy(policy)
    if policy == "full_identity_raw":
        _validate_raw_weights(identity_weight, semantic_weight)
        return item_export

    movie_count = len(item_export.movie_ids)
    source = item_export.item_features.tocsr(copy=False).astype(np.float32)
    semantic = _l1_normalize_rows(source[:, movie_count:]) * semantic_weight
    if policy == "full_identity_normalized":
        retained_rows = np.arange(movie_count, dtype=np.int32)
    elif policy == "supported_identity_normalized":
        retained_rows = np.asarray(
            [
                row
                for row, movie_id in enumerate(item_export.movie_ids)
                if movie_id in supported_movie_ids
            ],
            dtype=np.int32,
        )
    else:
        retained_rows = np.empty(0, dtype=np.int32)

    identity = csr_matrix(
        (
            np.full(retained_rows.size, identity_weight, dtype=np.float32),
            (retained_rows, retained_rows),
        ),
        shape=(movie_count, movie_count),
        dtype=np.float32,
    )
    matrix = hstack((identity, semantic), format="csr", dtype=np.float32)
    matrix.sum_duplicates()
    matrix.sort_indices()
    export_hash = _hash_transformed_export(
        parent_export_hash=item_export.manifest.export_hash,
        policy=policy,
        matrix=matrix,
    )
    manifest = replace(
        item_export.manifest,
        exporter_version=f"{item_export.manifest.exporter_version}+representation-v1",
        matrix_nnz=int(matrix.nnz),
        export_hash=export_hash,
        representation_policy=policy,
        identity_block_weight=(0.0 if policy == "metadata_only_normalized" else identity_weight),
        semantic_block_weight=semantic_weight,
    )
    return replace(item_export, item_features=matrix, manifest=manifest)


def transform_user_feature_export(
    user_export: UserFeatureExport,
    *,
    policy: FeatureRepresentationPolicy,
    identity_weight: float = 1.0,
    semantic_weight: float = 1.0,
) -> UserFeatureExport:
    _validate_policy(policy)
    if policy == "full_identity_raw":
        _validate_raw_weights(identity_weight, semantic_weight)
        return user_export

    user_count = len(user_export.user_ids)
    source = user_export.user_features.tocsr(copy=False).astype(np.float32)
    semantic = _l1_normalize_rows(source[:, user_count:]) * semantic_weight
    retained_rows = np.arange(user_count, dtype=np.int32)
    identity = csr_matrix(
        (
            np.full(user_count, identity_weight, dtype=np.float32),
            (retained_rows, retained_rows),
        ),
        shape=(user_count, user_count),
        dtype=np.float32,
    )
    matrix = hstack((identity, semantic), format="csr", dtype=np.float32)
    matrix.sum_duplicates()
    matrix.sort_indices()
    export_hash = hash_user_feature_export(
        user_mapping_hash=user_export.manifest.user_mapping_hash,
        feature_mapping_hash=user_export.manifest.feature_mapping_hash,
        item_feature_export_hash=user_export.manifest.item_feature_export_hash,
        matrix=matrix,
    )
    manifest = replace(
        user_export.manifest,
        exporter_version=f"{user_export.manifest.exporter_version}+representation-v1",
        matrix_nnz=int(matrix.nnz),
        export_hash=export_hash,
        representation_policy=policy,
        identity_block_weight=identity_weight,
        semantic_block_weight=semantic_weight,
    )
    return replace(user_export, user_features=matrix, manifest=manifest)


def sparse_row_sum_diagnostics(matrix: csr_matrix) -> dict[str, float]:
    row_sums = np.asarray(matrix.sum(axis=1), dtype=np.float64).reshape(-1)
    nonzero = row_sums[row_sums > 0]
    return {
        "zero_row_count": int(row_sums.size - nonzero.size),
        "min_nonzero": float(nonzero.min()) if nonzero.size else 0.0,
        "median": float(np.median(row_sums)),
        "p95": float(np.percentile(row_sums, 95)),
        "max": float(row_sums.max(initial=0.0)),
    }


def _l1_normalize_rows(matrix: csr_matrix) -> csr_matrix:
    normalized = matrix.tocsr(copy=True).astype(np.float32)
    row_sums = np.asarray(normalized.sum(axis=1), dtype=np.float64).reshape(-1)
    inverse = np.zeros_like(row_sums, dtype=np.float32)
    positive = row_sums > 0
    inverse[positive] = 1.0 / row_sums[positive]
    normalized = normalized.multiply(inverse[:, None]).tocsr()
    normalized.eliminate_zeros()
    normalized.sort_indices()
    return normalized


def _hash_transformed_export(
    *,
    parent_export_hash: str,
    policy: str,
    matrix: csr_matrix,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"item-feature-representation-v1\n")
    digest.update(f"parent:{parent_export_hash}\n".encode())
    digest.update(f"policy:{policy}\n".encode())
    digest.update(np.asarray(matrix.indptr, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.indices, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.data, dtype="<f4").tobytes())
    return digest.hexdigest()


def _validate_policy(policy: str) -> None:
    if policy not in FEATURE_REPRESENTATION_POLICIES:
        raise ValueError(f"unknown feature representation policy: {policy}")


def _validate_raw_weights(identity_weight: float, semantic_weight: float) -> None:
    if identity_weight != 1.0 or semantic_weight != 1.0:
        raise ValueError("raw feature representation does not accept block weight overrides")
