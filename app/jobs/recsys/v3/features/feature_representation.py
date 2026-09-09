from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import math
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
    "supported_identity_field_budgeted",
    "metadata_only_normalized",
]

FEATURE_REPRESENTATION_POLICIES: tuple[FeatureRepresentationPolicy, ...] = (
    "full_identity_raw",
    "full_identity_normalized",
    "supported_identity_normalized",
    "supported_identity_field_budgeted",
    "metadata_only_normalized",
)

ITEM_SEMANTIC_FIELDS = ("genre", "keyword", "actor", "director", "theme", "mood")
KeywordWeightingPolicy = Literal["none", "normalized_idf"]


def transform_item_feature_export(
    item_export: ItemFeatureExport,
    *,
    policy: FeatureRepresentationPolicy,
    supported_movie_ids: frozenset[int] = frozenset(),
    identity_weight: float = 1.0,
    semantic_weight: float = 1.0,
    field_budgets: Mapping[str, float] | None = None,
    keyword_weighting: KeywordWeightingPolicy = "none",
) -> ItemFeatureExport:
    _validate_policy(policy)
    if policy == "full_identity_raw":
        _validate_raw_weights(identity_weight, semantic_weight)
        return item_export

    movie_count = len(item_export.movie_ids)
    source = item_export.item_features.tocsr(copy=False).astype(np.float32)
    if policy == "supported_identity_field_budgeted":
        budgets = _validate_field_budgets(field_budgets)
        semantic = _field_budget_semantics(
            source[:, movie_count:],
            feature_tokens=item_export.feature_tokens[movie_count:],
            field_budgets=budgets,
            keyword_weighting=keyword_weighting,
        ) * semantic_weight
    else:
        budgets = {}
        if keyword_weighting != "none":
            raise ValueError("keyword weighting requires field-budgeted representation")
        semantic = _l1_normalize_rows(source[:, movie_count:]) * semantic_weight
    if policy == "full_identity_normalized":
        retained_rows = np.arange(movie_count, dtype=np.int32)
    elif policy in {
        "supported_identity_normalized",
        "supported_identity_field_budgeted",
    }:
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
        exporter_version=(
            f"{item_export.manifest.exporter_version}+representation-v2"
            if policy == "supported_identity_field_budgeted"
            else f"{item_export.manifest.exporter_version}+representation-v1"
        ),
        matrix_nnz=int(matrix.nnz),
        export_hash=export_hash,
        representation_policy=policy,
        identity_block_weight=(0.0 if policy == "metadata_only_normalized" else identity_weight),
        semantic_block_weight=semantic_weight,
        semantic_field_budgets=dict(budgets),
        keyword_weighting_policy=keyword_weighting,
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
        exporter_version=user_export.manifest.exporter_version.split("+", 1)[0],
        onboarding_profile_signature_hash=(
            user_export.manifest.onboarding_profile_signature_hash
        ),
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


def _field_budget_semantics(
    matrix: csr_matrix,
    *,
    feature_tokens: tuple[str, ...],
    field_budgets: Mapping[str, float],
    keyword_weighting: KeywordWeightingPolicy,
) -> csr_matrix:
    if matrix.shape[1] != len(feature_tokens):
        raise ValueError("semantic feature tokens do not match matrix columns")
    if keyword_weighting not in {"none", "normalized_idf"}:
        raise ValueError(f"unknown keyword weighting policy: {keyword_weighting}")

    family_by_column = np.empty(len(feature_tokens), dtype=np.int8)
    family_codes = {name: index for index, name in enumerate(ITEM_SEMANTIC_FIELDS)}
    for column, token in enumerate(feature_tokens):
        family = token.split(":", 1)[0]
        try:
            family_by_column[column] = family_codes[family]
        except KeyError as exc:
            raise ValueError(f"unsupported item semantic feature family: {family}") from exc

    weighted = matrix.tocoo(copy=True).astype(np.float32)
    keyword_code = family_codes["keyword"]
    keyword_idf = (
        _normalized_idf(weighted, column_count=matrix.shape[1])
        if keyword_weighting == "normalized_idf"
        else np.ones(matrix.shape[1], dtype=np.float32)
    )
    for family, code in family_codes.items():
        selected = family_by_column[weighted.col] == code
        if not np.any(selected):
            continue
        rows = weighted.row[selected]
        counts = np.bincount(rows, minlength=matrix.shape[0]).astype(np.float32)
        multipliers = np.full(rows.size, float(field_budgets[family]), dtype=np.float32)
        multipliers /= counts[rows]
        if code == keyword_code:
            multipliers *= keyword_idf[weighted.col[selected]]
        weighted.data[selected] *= multipliers

    result = weighted.tocsr()
    result.eliminate_zeros()
    result.sum_duplicates()
    result.sort_indices()
    return result


def _normalized_idf(matrix, *, column_count: int) -> np.ndarray:
    movie_count = matrix.shape[0]
    if movie_count <= 1:
        return np.ones(column_count, dtype=np.float32)
    document_frequency = np.bincount(matrix.col, minlength=column_count).astype(np.float64)
    denominator = math.log((movie_count + 1.0) / 2.0)
    idf = np.zeros(column_count, dtype=np.float64)
    present = document_frequency > 0.0
    idf[present] = np.log(
        (movie_count + 1.0) / (document_frequency[present] + 1.0)
    ) / denominator
    return np.clip(idf, 0.0, 1.0).astype(np.float32)


def _validate_field_budgets(
    field_budgets: Mapping[str, float] | None,
) -> dict[str, float]:
    if field_budgets is None:
        raise ValueError("field-budgeted representation requires semantic field budgets")
    unknown = set(field_budgets) - set(ITEM_SEMANTIC_FIELDS)
    missing = set(ITEM_SEMANTIC_FIELDS) - set(field_budgets)
    if unknown or missing:
        raise ValueError(
            f"semantic field budgets mismatch missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    budgets = {name: float(field_budgets[name]) for name in ITEM_SEMANTIC_FIELDS}
    if any(not math.isfinite(value) or value < 0.0 for value in budgets.values()):
        raise ValueError("semantic field budgets must be finite and non-negative")
    if not math.isclose(sum(budgets.values()), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("semantic field budgets must sum to 1")
    return budgets


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
