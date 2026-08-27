from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.features.feature_schemas import (
    ItemFeatureExport,
    UserFeatureExport,
    UserFeatureManifest,
)
from app.models.mapping import user_favorite_movies, user_genres
from app.models.movie import Movie
from app.services.recsys.v3.domain.catalog import eligible_catalog_movie_clause
from app.services.recsys.v3.config import (
    USER_FEATURE_EXPLICIT_GENRE_WEIGHT,
    USER_FEATURE_EXPORTER_VERSION,
    USER_FEATURE_FAVORITE_DERIVED_WEIGHT,
)
from app.services.recsys.v3.domain.feature_registry import FeatureName, get_feature_definition


def export_user_features(
    db: Session,
    *,
    user_ids: tuple[int, ...],
    item_export: ItemFeatureExport,
) -> UserFeatureExport:
    validate_ordered_ids(user_ids, "user")
    if not user_ids:
        raise ValueError("user feature export requires at least one user")
    validate_item_identity_columns(item_export)

    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    explicit_genre_rows = tuple(
        (int(user_id), int(genre_id))
        for user_id, genre_id in db.execute(
            select(user_genres.c.user_id, user_genres.c.genre_id)
            .where(user_genres.c.user_id.in_(user_ids))
            .order_by(user_genres.c.user_id, user_genres.c.genre_id)
        )
    )
    favorite_rows = tuple(
        (int(user_id), int(movie_id))
        for user_id, movie_id in db.execute(
            select(user_favorite_movies.c.user_id, user_favorite_movies.c.movie_id)
            .join(Movie, Movie.id == user_favorite_movies.c.movie_id)
            .where(
                user_favorite_movies.c.user_id.in_(user_ids),
                *eligible_catalog_movie_clause(),
            )
            .order_by(user_favorite_movies.c.user_id, user_favorite_movies.c.movie_id)
        )
    )
    return build_user_feature_export(
        user_ids=user_ids,
        explicit_genre_rows=explicit_genre_rows,
        favorite_rows=favorite_rows,
        item_export=item_export,
    )


def build_user_feature_export(
    *,
    user_ids: tuple[int, ...],
    explicit_genre_rows: Iterable[tuple[int, int]],
    favorite_rows: Iterable[tuple[int, int]],
    item_export: ItemFeatureExport,
) -> UserFeatureExport:
    validate_ordered_ids(user_ids, "user")
    if not user_ids:
        raise ValueError("user feature export requires at least one user")
    validate_item_identity_columns(item_export)
    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    explicit_rows = tuple(sorted(set(explicit_genre_rows)))
    favorite_pairs = tuple(sorted(set(favorite_rows)))
    if any(user_id not in user_id_map or genre_id <= 0 for user_id, genre_id in explicit_rows):
        raise ValueError("explicit genre rows contain an invalid user or genre")
    if any(user_id not in user_id_map or movie_id <= 0 for user_id, movie_id in favorite_pairs):
        raise ValueError("favorite rows contain an invalid user or movie")

    movie_identity_count = len(item_export.movie_ids)
    item_tokens = item_export.feature_tokens
    genre_prefix = f"{get_feature_definition(FeatureName.GENRE).namespace}:"
    all_genre_tokens = tuple(
        token for token in item_tokens[movie_identity_count:] if token.startswith(genre_prefix)
    )
    values_by_user_token: dict[tuple[int, str], float] = {}
    for user_id, genre_id in explicit_rows:
        token = get_feature_definition(FeatureName.GENRE).token(genre_id)
        if token in item_export.feature_token_map:
            values_by_user_token[(user_id, token)] = USER_FEATURE_EXPLICIT_GENRE_WEIGHT

    favorite_derived_pair_count = 0
    missing_favorite_movie_count = 0
    item_matrix = item_export.item_features.tocsr(copy=False)
    for user_id, movie_id in favorite_pairs:
        movie_index = item_export.movie_id_map.get(movie_id)
        if movie_index is None:
            missing_favorite_movie_count += 1
            continue
        start = int(item_matrix.indptr[movie_index])
        end = int(item_matrix.indptr[movie_index + 1])
        for feature_index, item_value in zip(
            item_matrix.indices[start:end],
            item_matrix.data[start:end],
            strict=True,
        ):
            if feature_index < movie_identity_count:
                continue
            token = item_tokens[int(feature_index)]
            value = USER_FEATURE_FAVORITE_DERIVED_WEIGHT * float(item_value)
            key = (user_id, token)
            values_by_user_token[key] = max(values_by_user_token.get(key, 0.0), value)
            favorite_derived_pair_count += 1

    identity_tokens = tuple(
        get_feature_definition(FeatureName.USER_IDENTITY).token(user_id)
        for user_id in user_ids
    )
    observed_tokens = {token for _user_id, token in values_by_user_token}
    selected_shared_tokens = tuple(
        token
        for token in item_tokens[movie_identity_count:]
        if token in observed_tokens or token in all_genre_tokens
    )
    feature_tokens = identity_tokens + selected_shared_tokens
    feature_token_map = {token: index for index, token in enumerate(feature_tokens)}
    if len(feature_token_map) != len(feature_tokens):
        raise ValueError("duplicate LightFM user feature token")

    row_indices = list(range(len(user_ids)))
    column_indices = list(range(len(user_ids)))
    data = [1.0] * len(user_ids)
    covered_users: set[int] = set()
    for (user_id, token), value in sorted(
        values_by_user_token.items(),
        key=lambda item: (user_id_map[item[0][0]], feature_token_map.get(item[0][1], -1)),
    ):
        feature_index = feature_token_map.get(token)
        if feature_index is None or value <= 0 or not np.isfinite(value):
            continue
        row_indices.append(user_id_map[user_id])
        column_indices.append(feature_index)
        data.append(value)
        covered_users.add(user_id)
    matrix = coo_matrix(
        (
            np.asarray(data, dtype=np.float32),
            (
                np.asarray(row_indices, dtype=np.int32),
                np.asarray(column_indices, dtype=np.int32),
            ),
        ),
        shape=(len(user_ids), len(feature_tokens)),
        dtype=np.float32,
    ).tocsr()
    matrix.sum_duplicates()
    matrix.sort_indices()

    user_mapping_hash = hash_ordered_values("user", (str(item) for item in user_ids))
    feature_mapping_hash = hash_ordered_values("user_feature", feature_tokens)
    export_hash = hash_user_feature_export(
        user_mapping_hash=user_mapping_hash,
        feature_mapping_hash=feature_mapping_hash,
        item_feature_export_hash=item_export.manifest.export_hash,
        matrix=matrix,
    )
    family_counts = Counter(token.split(":", 1)[0] for token in selected_shared_tokens)
    manifest = UserFeatureManifest(
        exporter_version=USER_FEATURE_EXPORTER_VERSION,
        ontology_build_id=item_export.manifest.ontology_build_id,
        ontology_source_hash=item_export.manifest.ontology_source_hash,
        item_feature_export_hash=item_export.manifest.export_hash,
        user_count=len(user_ids),
        feature_count=len(feature_tokens),
        matrix_nnz=int(matrix.nnz),
        matrix_shape=(int(matrix.shape[0]), int(matrix.shape[1])),
        user_mapping_hash=user_mapping_hash,
        feature_mapping_hash=feature_mapping_hash,
        export_hash=export_hash,
        explicit_genre_pair_count=len(explicit_rows),
        favorite_movie_pair_count=len(favorite_pairs),
        favorite_derived_pair_count=favorite_derived_pair_count,
        covered_user_count=len(covered_users),
        missing_favorite_movie_count=missing_favorite_movie_count,
        feature_family_counts=dict(sorted(family_counts.items())),
        explicit_genre_weight=USER_FEATURE_EXPLICIT_GENRE_WEIGHT,
        favorite_derived_weight=USER_FEATURE_FAVORITE_DERIVED_WEIGHT,
        vocabulary_policy="identity_all_genres_observed_favorite_features",
    )
    return UserFeatureExport(
        user_ids=user_ids,
        user_id_map=user_id_map,
        feature_tokens=feature_tokens,
        feature_token_map=feature_token_map,
        user_features=matrix,
        manifest=manifest,
    )


def validate_item_identity_columns(item_export: ItemFeatureExport) -> None:
    movie_count = len(item_export.movie_ids)
    expected = tuple(
        get_feature_definition(FeatureName.MOVIE_IDENTITY).token(movie_id)
        for movie_id in item_export.movie_ids
    )
    if item_export.feature_tokens[:movie_count] != expected:
        raise ValueError("item feature export does not preserve leading movie identity columns")


def validate_ordered_ids(ids: tuple[int, ...], label: str) -> None:
    if any(value <= 0 for value in ids):
        raise ValueError(f"{label} IDs must be positive")
    if any(current >= following for current, following in zip(ids, ids[1:], strict=False)):
        raise ValueError(f"{label} IDs must be strictly increasing")


def hash_ordered_values(label: str, values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{label}\n".encode())
    for value in values:
        digest.update(f"{value}\n".encode())
    return digest.hexdigest()


def hash_user_feature_export(
    *,
    user_mapping_hash: str,
    feature_mapping_hash: str,
    item_feature_export_hash: str,
    matrix: csr_matrix,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"exporter:{USER_FEATURE_EXPORTER_VERSION}\n".encode())
    digest.update(f"users:{user_mapping_hash}\n".encode())
    digest.update(f"features:{feature_mapping_hash}\n".encode())
    digest.update(f"item_export:{item_feature_export_hash}\n".encode())
    digest.update(np.asarray(matrix.indptr, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.indices, dtype="<i8").tobytes())
    digest.update(np.asarray(matrix.data, dtype="<f4").tobytes())
    return digest.hexdigest()
