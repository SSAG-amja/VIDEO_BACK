from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.recsys.v3.config import SHORT_TERM_RETRIEVAL_LIMIT
from app.services.recsys.v3.domain.feature_registry import get_feature_definition
from app.services.recsys.v3.profiles.profile_builder import validate_profile_build
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    ShortTermCandidate,
    ShortTermRetrievalDiagnostics,
    ShortTermRetrievalResult,
)
from app.services.recsys.v3.domain.schemas import ProfileFeatureSignal, UserProfileBundle


def retrieve_short_term_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    limit: int = SHORT_TERM_RETRIEVAL_LIMIT,
) -> ShortTermRetrievalResult:
    if limit <= 0 or limit > SHORT_TERM_RETRIEVAL_LIMIT:
        raise ValueError(f"short-term retrieval limit must be between 1 and {SHORT_TERM_RETRIEVAL_LIMIT}")
    started = time.monotonic()
    validate_profile_build(db, ontology_build_id)
    feature_rows = build_short_term_feature_rows(profile.short_term.positive_features)
    excluded_movie_ids = frozenset(
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
    )
    rows = load_short_term_candidate_rows(
        db,
        ontology_build_id=ontology_build_id,
        feature_rows=feature_rows,
        excluded_movie_ids=excluded_movie_ids,
        limit=limit,
    )
    candidates = tuple(
        ShortTermCandidate(
            movie_id=int(movie_id),
            short_term_raw_score=float(raw_score),
            source_rank=rank,
        )
        for rank, (movie_id, raw_score) in enumerate(rows, start=1)
    )
    return ShortTermRetrievalResult(
        candidates=candidates,
        diagnostics=ShortTermRetrievalDiagnostics(
            ontology_build_id=ontology_build_id,
            profile_feature_count=len(feature_rows),
            excluded_movie_count=len(excluded_movie_ids),
            candidate_count=len(candidates),
            elapsed_seconds=round(time.monotonic() - started, 6),
            query_count=1 + int(bool(feature_rows)),
        ),
    )


def build_short_term_feature_rows(
    features: tuple[ProfileFeatureSignal, ...],
) -> tuple[tuple[str, str, str, str, float], ...]:
    rows: dict[tuple[str, str, str, str], float] = {}
    for signal in features:
        definition = get_feature_definition(signal.feature)
        relations = definition.ontology_relations
        if len(relations) != 1:
            raise ValueError(
                f"short-term feature requires one ontology relation feature={signal.feature.value}"
            )
        if definition.ontology_node_type is None:
            raise ValueError(
                f"short-term feature requires an ontology node type feature={signal.feature.value}"
            )
        key = (
            relations[0],
            signal.feature.value,
            definition.ontology_node_type,
            signal.ref_id,
        )
        rows[key] = max(rows.get(key, 0.0), float(signal.score))
    return tuple((*key, score) for key, score in sorted(rows.items()))


def load_short_term_candidate_rows(
    db: Session,
    *,
    ontology_build_id: int,
    feature_rows: tuple[tuple[str, str, str, str, float], ...],
    excluded_movie_ids: frozenset[int],
    limit: int,
) -> list[tuple[int, float]]:
    if not feature_rows:
        return []
    relation_types, feature_names, node_types, ref_ids, profile_scores = zip(
        *feature_rows,
        strict=True,
    )
    rows = db.execute(
        text(
            """
            WITH profile_feature(
                relation_type, feature_name, node_type, ref_id, profile_score
            ) AS (
                SELECT *
                FROM unnest(
                    CAST(:relation_types AS text[]),
                    CAST(:feature_names AS text[]),
                    CAST(:node_types AS text[]),
                    CAST(:ref_ids AS text[]),
                    CAST(:profile_scores AS double precision[])
                )
            ),
            profile_nodes AS MATERIALIZED (
                SELECT profile_feature.*,
                       node.id AS feature_node_id
                FROM profile_feature
                JOIN ontology_nodes node
                  ON node.build_id = :build_id
                 AND node.node_type = profile_feature.node_type
                 AND node.ref_id = profile_feature.ref_id
                 AND node.is_active IS TRUE
            ),
            matched_type_aggregates AS (
                SELECT movie.id AS movie_id,
                       profile_node.feature_name,
                       sum(
                           profile_node.profile_score
                           * COALESCE(edge.effective_strength, edge.weight * edge.confidence)
                       ) AS raw_score,
                       max(
                           profile_node.profile_score
                           * COALESCE(edge.effective_strength, edge.weight * edge.confidence)
                       ) AS peak_score
                FROM profile_nodes profile_node
                JOIN ontology_edges edge
                  ON edge.build_id = :build_id
                 AND edge.target_node_id = profile_node.feature_node_id
                 AND edge.relation_type = profile_node.relation_type
                JOIN ontology_nodes movie_node
                  ON movie_node.id = edge.source_node_id
                 AND movie_node.build_id = :build_id
                 AND movie_node.node_type = 'movie'
                 AND movie_node.is_active IS TRUE
                JOIN movies movie
                  ON movie.id::text = movie_node.ref_id
                WHERE movie.id <> ALL(CAST(:excluded_movie_ids AS integer[]))
                  AND movie.adult IS FALSE
                  AND COALESCE(
                      NULLIF(trim(movie.title_ko), ''),
                      NULLIF(trim(movie.title), '')
                  ) IS NOT NULL
                GROUP BY movie.id, profile_node.feature_name
            ),
            matched_type_scores AS (
                SELECT movie_id,
                       peak_score * (
                           1.0 + ln(greatest(raw_score / peak_score, 1.0))
                       ) AS type_score
                FROM matched_type_aggregates
                WHERE raw_score > 0.0
                  AND peak_score > 0.0
            )
            SELECT movie_id,
                   sum(type_score) AS short_term_raw_score
            FROM matched_type_scores
            GROUP BY movie_id
            ORDER BY round(sum(type_score)::numeric, 12) DESC,
                     movie_id ASC
            LIMIT :candidate_limit
            """
        ),
        {
            "build_id": ontology_build_id,
            "relation_types": list(relation_types),
            "feature_names": list(feature_names),
            "node_types": list(node_types),
            "ref_ids": list(ref_ids),
            "profile_scores": list(profile_scores),
            "excluded_movie_ids": list(sorted(excluded_movie_ids)) or [-1],
            "candidate_limit": limit,
        },
    )
    return [(int(movie_id), float(score)) for movie_id, score in rows]
