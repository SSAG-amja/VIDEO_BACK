from __future__ import annotations

import time
from collections.abc import Container

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud.recsys.movies import load_quality_fallback_movies
from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    COLD_START_GENRE_COVERAGE_WEIGHT,
    COLD_START_GENRE_ONLY_MIN_VOTE_COUNT,
    COLD_START_GENRE_ONLY_QUALITY_WEIGHT,
    COLD_START_GENRE_ONLY_SEMANTIC_WEIGHT,
    COLD_START_GENRE_ONLY_TRUSTED_VOTE_COUNT,
    COLD_START_GENRE_SPECIFICITY_WEIGHT,
    COLD_START_OVERVIEW_SUPPORT_BONUS_MAX,
    POLICY_QUALITY_POPULARITY_REFERENCE,
    POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR,
)
from app.services.recsys.v3.profiles.profile_builder import (
    build_onboarding_feature_signals,
    validate_profile_build,
)
from app.services.recsys.v3.policy.quality import reliable_quality_score
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateSource,
    ColdStartCandidate,
    ColdStartRetrievalDiagnostics,
    ColdStartRetrievalResult,
    ColdStartStrategy,
)
from app.services.recsys.v3.domain.schemas import ProfileFeatureSignal, UserProfileBundle
from app.services.recsys.v3.retrieval.short_term_retriever import (
    build_short_term_feature_rows,
    load_short_term_candidate_rows,
)


def retrieve_cold_start_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    profile: UserProfileBundle,
    model_known_movie_ids: Container[int],
    limit: int = CANDIDATE_POOL_SIZE,
) -> ColdStartRetrievalResult:
    if limit <= 0 or limit > CANDIDATE_POOL_SIZE:
        raise ValueError(f"cold-start limit must be between 1 and {CANDIDATE_POOL_SIZE}")
    started = time.monotonic()
    excluded = (
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
        | profile.onboarding.favorite_movie_ids
    )
    features = _onboarding_features(profile)
    feature_rows = build_short_term_feature_rows(features)
    if feature_rows:
        validate_profile_build(db, ontology_build_id)
        require_direct_genre_match = (
            bool(profile.onboarding.genre_ids)
            and not profile.onboarding.favorite_movie_ids
        )
        if require_direct_genre_match:
            rows = load_cold_start_candidate_rows(
                db,
                ontology_build_id=ontology_build_id,
                feature_rows=feature_rows,
                excluded_movie_ids=excluded,
                limit=limit,
                require_direct_genre_match=True,
            )
        else:
            rows = [
                (movie_id, raw_score, 0.0, None, 0.0, 0.0, False)
                for movie_id, raw_score in load_short_term_candidate_rows(
                    db,
                    ontology_build_id=ontology_build_id,
                    feature_rows=feature_rows,
                    excluded_movie_ids=excluded,
                    limit=limit,
                )
            ]
        if rows:
            candidates = tuple(
                ColdStartCandidate(
                    movie_id=movie_id,
                    raw_score=raw_score,
                    overview_support_score=overview_support_score,
                    rule_selection_score=rule_selection_score,
                    quality_score=quality_score,
                    genre_relevance_score=genre_relevance_score,
                    trusted_quality=trusted_quality,
                    source_rank=rank,
                    source=(
                        CandidateSource.COLD_START
                        if movie_id in model_known_movie_ids
                        else CandidateSource.ONTOLOGY_COLD_ITEM
                    ),
                )
                for rank, (
                    movie_id,
                    raw_score,
                    overview_support_score,
                    rule_selection_score,
                    quality_score,
                    genre_relevance_score,
                    trusted_quality,
                ) in enumerate(
                    rows,
                    start=1,
                )
            )
            return _result(
                ontology_build_id=ontology_build_id,
                strategy=ColdStartStrategy.ONTOLOGY_RULE,
                feature_count=len(feature_rows),
                excluded_count=len(excluded),
                candidates=candidates,
                query_count=2,
                started=started,
            )

    movies = load_quality_fallback_movies(
        db,
        excluded_movie_ids=set(excluded),
        limit=limit,
    )
    quality_rows = sorted(
        (
            (
                int(movie.id),
                reliable_quality_score(
                    popularity=float(movie.popularity or 0.0),
                    vote_average=float(movie.vote_average or 0.0),
                    vote_count=int(movie.vote_count or 0),
                ),
                int(movie.vote_count or 0),
            )
            for movie in movies
        ),
        key=lambda item: (-item[1], item[0]),
    )
    candidates = tuple(
        ColdStartCandidate(
            movie_id=movie_id,
            raw_score=raw_score,
            rule_selection_score=raw_score,
            quality_score=raw_score,
            trusted_quality=(vote_count >= COLD_START_GENRE_ONLY_TRUSTED_VOTE_COUNT),
            source_rank=rank,
            source=CandidateSource.COLD_START,
        )
        for rank, (movie_id, raw_score, vote_count) in enumerate(quality_rows, start=1)
    )
    return _result(
        ontology_build_id=ontology_build_id,
        strategy=ColdStartStrategy.QUALITY_FALLBACK,
        feature_count=len(feature_rows),
        excluded_count=len(excluded),
        candidates=candidates,
        query_count=(2 if feature_rows else 0) + 1,
        started=started,
    )


def _onboarding_features(profile: UserProfileBundle) -> tuple[ProfileFeatureSignal, ...]:
    features = {
        (item.feature, item.ref_id): item
        for item in (
            *build_onboarding_feature_signals(profile.onboarding),
            *profile.short_term.positive_features,
        )
    }
    return tuple(features[key] for key in sorted(features, key=lambda item: (item[0].value, item[1])))


def load_cold_start_candidate_rows(
    db: Session,
    *,
    ontology_build_id: int,
    feature_rows: tuple[tuple[str, str, str, str, float], ...],
    excluded_movie_ids: frozenset[int],
    limit: int,
    require_direct_genre_match: bool,
) -> list[tuple[int, float, float, float, float, float, bool]]:
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
            direct_profile_nodes AS MATERIALIZED (
                SELECT profile_feature.relation_type,
                       profile_feature.feature_name,
                       node.id AS feature_node_id,
                       profile_feature.profile_score,
                       false AS requires_overview
                FROM profile_feature
                JOIN ontology_nodes node
                  ON node.build_id = :build_id
                 AND node.node_type = profile_feature.node_type
                 AND node.ref_id = profile_feature.ref_id
                 AND node.is_active IS TRUE
            ),
            expanded_genre_nodes AS MATERIALIZED (
                SELECT CASE semantic_edge.relation_type
                           WHEN 'suggests_theme' THEN 'has_theme'
                           WHEN 'suggests_mood' THEN 'has_mood'
                       END AS relation_type,
                       CASE semantic_edge.relation_type
                           WHEN 'suggests_theme' THEN 'theme'
                           WHEN 'suggests_mood' THEN 'mood'
                       END AS feature_name,
                       semantic_edge.target_node_id AS feature_node_id,
                       genre_node.profile_score
                       * COALESCE(
                           semantic_edge.effective_strength,
                           semantic_edge.weight * semantic_edge.confidence
                       ) AS profile_score,
                       true AS requires_overview
                FROM direct_profile_nodes genre_node
                JOIN ontology_edges semantic_edge
                  ON semantic_edge.build_id = :build_id
                 AND semantic_edge.source_node_id = genre_node.feature_node_id
                 AND semantic_edge.relation_type IN ('suggests_theme', 'suggests_mood')
                WHERE genre_node.feature_name = 'genre'
            ),
            retrieval_nodes AS MATERIALIZED (
                SELECT relation_type,
                       feature_name,
                       feature_node_id,
                       max(profile_score) AS profile_score,
                       bool_and(requires_overview) AS requires_overview
                FROM (
                    SELECT * FROM direct_profile_nodes
                    UNION ALL
                    SELECT * FROM expanded_genre_nodes
                ) nodes
                GROUP BY relation_type, feature_name, feature_node_id
            ),
            direct_genre_movies AS MATERIALIZED (
                SELECT DISTINCT edge.source_node_id AS movie_node_id
                FROM direct_profile_nodes genre_node
                JOIN ontology_edges edge
                  ON edge.build_id = :build_id
                 AND edge.target_node_id = genre_node.feature_node_id
                 AND edge.relation_type = 'has_genre'
                WHERE genre_node.feature_name = 'genre'
            ),
            preferred_genre_count AS (
                SELECT count(DISTINCT feature_node_id)::double precision AS value
                FROM direct_profile_nodes
                WHERE feature_name = 'genre'
            ),
            matched_edges AS (
                SELECT movie.id AS movie_id,
                       retrieval_node.feature_name,
                       edge.id AS edge_id,
                       retrieval_node.profile_score
                       * COALESCE(
                           edge.effective_strength,
                           edge.weight * edge.confidence
                       ) AS matched_score,
                       max(evidence.effective_strength) AS overview_strength,
                       COALESCE(movie.vote_count, 0) AS vote_count,
                       COALESCE(movie.vote_average, 0.0) AS vote_average,
                       COALESCE(movie.popularity, 0.0) AS popularity
                FROM retrieval_nodes retrieval_node
                JOIN ontology_edges edge
                  ON edge.build_id = :build_id
                 AND edge.target_node_id = retrieval_node.feature_node_id
                 AND edge.relation_type = retrieval_node.relation_type
                JOIN ontology_nodes movie_node
                  ON movie_node.id = edge.source_node_id
                 AND movie_node.build_id = :build_id
                 AND movie_node.node_type = 'movie'
                 AND movie_node.is_active IS TRUE
                JOIN movies movie
                  ON movie.id::text = movie_node.ref_id
                LEFT JOIN ontology_edge_evidence evidence
                  ON evidence.build_id = :build_id
                 AND evidence.edge_id = edge.id
                 AND evidence.evidence_type = 'overview_signal'
                WHERE movie.id <> ALL(CAST(:excluded_movie_ids AS integer[]))
                  AND movie.adult IS FALSE
                  AND COALESCE(
                      NULLIF(trim(movie.title_ko), ''),
                      NULLIF(trim(movie.title), '')
                  ) IS NOT NULL
                  AND (
                      retrieval_node.requires_overview IS FALSE
                      OR evidence.id IS NOT NULL
                  )
                  AND (
                      CAST(:require_direct_genre_match AS boolean) IS FALSE
                      OR movie_node.id IN (SELECT movie_node_id FROM direct_genre_movies)
                  )
                  AND (
                      CAST(:require_direct_genre_match AS boolean) IS FALSE
                      OR COALESCE(movie.vote_count, 0) >= :minimum_vote_count
                  )
                GROUP BY movie.id,
                         retrieval_node.feature_name,
                         edge.id,
                         retrieval_node.profile_score,
                         edge.effective_strength,
                         edge.weight,
                         edge.confidence,
                         movie.vote_count,
                         movie.vote_average,
                         movie.popularity
            ),
            matched_type_aggregates AS (
                SELECT movie_id,
                       feature_name,
                       sum(matched_score) AS raw_score,
                       max(matched_score) AS peak_score,
                       count(DISTINCT edge_id) AS matched_feature_count,
                       sum(
                           matched_score
                           * :overview_bonus_max
                           * COALESCE(overview_strength, 0.0)
                       ) AS overview_raw_score,
                       max(
                           matched_score
                           * :overview_bonus_max
                           * COALESCE(overview_strength, 0.0)
                       ) AS overview_peak_score,
                       max(vote_count) AS vote_count,
                       max(vote_average) AS vote_average,
                       max(popularity) AS popularity
                FROM matched_edges
                GROUP BY movie_id, feature_name
            ),
            matched_type_scores AS (
                SELECT movie_id,
                       feature_name,
                       peak_score * (
                           1.0 + ln(greatest(raw_score / peak_score, 1.0))
                       ) AS type_score,
                       CASE
                           WHEN overview_raw_score > 0.0 AND overview_peak_score > 0.0
                           THEN overview_peak_score * (
                               1.0 + ln(greatest(
                                   overview_raw_score / overview_peak_score,
                                   1.0
                               ))
                           )
                           ELSE 0.0
                       END AS overview_support_score,
                       matched_feature_count,
                       vote_count,
                       vote_average,
                       popularity
                FROM matched_type_aggregates
                WHERE raw_score > 0.0
                  AND peak_score > 0.0
            ),
            candidate_scores AS (
                SELECT movie_id,
                       sum(type_score) + sum(overview_support_score) AS raw_score,
                       sum(overview_support_score) AS overview_support_score,
                       max(matched_feature_count) FILTER (
                           WHERE feature_name = 'genre'
                       ) AS matched_genre_count,
                       max(vote_count) AS vote_count,
                       max(vote_average) AS vote_average,
                       max(popularity) AS popularity
                FROM matched_type_scores
                GROUP BY movie_id
            ),
            candidate_metrics AS (
                SELECT candidate.movie_id,
                       candidate.raw_score,
                       candidate.overview_support_score,
                       candidate.vote_count,
                       candidate.vote_average,
                       candidate.popularity,
                       candidate.matched_genre_count,
                       preferred_genre.value AS preferred_genre_count,
                       genre_count.total_genre_count,
                       (
                           :genre_coverage_weight
                           * candidate.matched_genre_count
                           / greatest(preferred_genre.value, 1.0)
                           + :genre_specificity_weight
                           * candidate.matched_genre_count
                           / greatest(genre_count.total_genre_count, 1.0)
                       ) AS genre_relevance_score,
                       (
                           candidate.vote_count::double precision
                           / (candidate.vote_count + :quality_vote_prior)
                       ) * (
                           0.85 * least(greatest(candidate.vote_average, 0.0) / 10.0, 1.0)
                           + 0.15 * least(
                               ln(1.0 + greatest(candidate.popularity, 0.0))
                               / ln(1.0 + :quality_popularity_reference),
                               1.0
                           )
                       ) AS quality_score
                FROM candidate_scores candidate
                CROSS JOIN preferred_genre_count preferred_genre
                JOIN LATERAL (
                    SELECT count(*)::double precision AS total_genre_count
                    FROM movie_genres
                    WHERE movie_genres.movie_id = candidate.movie_id
                ) genre_count ON TRUE
            ),
            normalized_candidates AS (
                SELECT candidate_metrics.*,
                       cume_dist() OVER (ORDER BY raw_score) AS semantic_percentile
                FROM candidate_metrics
            ),
            ranked_candidates AS (
                SELECT normalized_candidates.*,
                       :semantic_weight
                       * semantic_percentile
                       * genre_relevance_score
                       + :quality_weight * quality_score AS rule_selection_score,
                       vote_count >= :trusted_vote_count AS trusted_quality
                FROM normalized_candidates
            )
            SELECT movie_id,
                   raw_score,
                   overview_support_score,
                   rule_selection_score,
                   quality_score,
                   genre_relevance_score,
                   trusted_quality
            FROM ranked_candidates
            ORDER BY CASE WHEN trusted_quality THEN 0 ELSE 1 END,
                     round(rule_selection_score::numeric, 12) DESC,
                     vote_count DESC,
                     popularity DESC,
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
            "overview_bonus_max": COLD_START_OVERVIEW_SUPPORT_BONUS_MAX,
            "semantic_weight": COLD_START_GENRE_ONLY_SEMANTIC_WEIGHT,
            "quality_weight": COLD_START_GENRE_ONLY_QUALITY_WEIGHT,
            "genre_coverage_weight": COLD_START_GENRE_COVERAGE_WEIGHT,
            "genre_specificity_weight": COLD_START_GENRE_SPECIFICITY_WEIGHT,
            "minimum_vote_count": COLD_START_GENRE_ONLY_MIN_VOTE_COUNT,
            "trusted_vote_count": COLD_START_GENRE_ONLY_TRUSTED_VOTE_COUNT,
            "quality_vote_prior": POLICY_QUALITY_VOTE_CONFIDENCE_PRIOR,
            "quality_popularity_reference": POLICY_QUALITY_POPULARITY_REFERENCE,
            "require_direct_genre_match": require_direct_genre_match,
            "candidate_limit": limit,
        },
    )
    return [
        (
            int(movie_id),
            float(raw_score),
            float(overview_support_score),
            float(rule_selection_score),
            float(quality_score),
            float(genre_relevance_score),
            bool(trusted_quality),
        )
        for (
            movie_id,
            raw_score,
            overview_support_score,
            rule_selection_score,
            quality_score,
            genre_relevance_score,
            trusted_quality,
        ) in rows
    ]


def _result(
    *,
    ontology_build_id: int,
    strategy: ColdStartStrategy,
    feature_count: int,
    excluded_count: int,
    candidates: tuple[ColdStartCandidate, ...],
    query_count: int,
    started: float,
) -> ColdStartRetrievalResult:
    return ColdStartRetrievalResult(
        candidates=candidates,
        diagnostics=ColdStartRetrievalDiagnostics(
            ontology_build_id=ontology_build_id,
            strategy=strategy,
            profile_feature_count=feature_count,
            excluded_movie_count=excluded_count,
            candidate_count=len(candidates),
            ontology_cold_item_count=sum(
                item.source == CandidateSource.ONTOLOGY_COLD_ITEM for item in candidates
            ),
            query_count=query_count,
            elapsed_seconds=round(time.monotonic() - started, 6),
        ),
    )
