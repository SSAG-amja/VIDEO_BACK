from sqlalchemy.orm import Session
from sqlalchemy import desc, func, select, text

from app.crud.recsys.ontology import get_active_build
from app.models.mapping import movie_genres
from app.services.recsys.v2.config import (
    ALLOWED_CANDIDATE_STATUSES,
    DEFAULT_CANDIDATE_SLICE_SIZE,
    FALLBACK_NEGATIVE_PENALTY_SCALE,
    MAX_CANDIDATE_GENRE_COUNT,
    MAX_NEGATIVE_PENALTY_RATIO,
    MAX_NORMALIZED_POPULARITY,
    MIN_CANDIDATE_POPULARITY,
    MIN_CANDIDATE_RUNTIME,
    MIN_CANDIDATE_VOTE_COUNT,
    NEGATIVE_CANDIDATE_OVERSAMPLE_FACTOR,
    NEGATIVE_FEATURE_TYPE_WEIGHTS,
    PASSED_ACTION_WEIGHT,
    PASSED_FEATURE_SIGNAL_NORMALIZER,
    POPULARITY_NORMALIZATION_DIVISOR,
    PROFILE_FEATURE_LIMITS,
    RATING_NORMALIZATION_DIVISOR,
    SUBSCRIBED_OTT_SCORE_BONUS,
)
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile, UserProfile
from app.models.movie import Movie


GRAPH_FEATURE_TYPES = ("genre", "keyword", "actor", "director", "theme", "mood")


def generate_candidates(
    db: Session,
    *,
    profile: UserProfile,
    session_profile: SessionProfile,
    limit: int = DEFAULT_CANDIDATE_SLICE_SIZE,
    subscribed_only: bool = False,
) -> list[CandidateScore]:
    if subscribed_only and not profile.subscribed_ott_ids:
        return []

    active_build = get_active_build(db)
    excluded_movie_ids = profile.excluded_movie_ids | session_profile.recent_skipped_movie_ids
    if active_build is None:
        return generate_fallback_candidates(
            db,
            profile=profile,
            excluded_movie_ids=excluded_movie_ids,
            limit=limit,
            subscribed_only=subscribed_only,
            build_id=None,
        )

    feature_rows = build_profile_feature_rows(profile)
    if not feature_rows:
        return generate_fallback_candidates(
            db,
            profile=profile,
            excluded_movie_ids=excluded_movie_ids,
            limit=limit,
            subscribed_only=subscribed_only,
            build_id=active_build.id,
        )

    candidates = generate_graph_candidates(
        db,
        build_id=active_build.id,
        feature_rows=feature_rows,
        profile=profile,
        excluded_movie_ids=excluded_movie_ids,
        limit=limit,
        subscribed_only=subscribed_only,
    )
    if len(candidates) < limit:
        seen_movie_ids = {candidate.movie_id for candidate in candidates}
        candidates.extend(
            generate_fallback_candidates(
                db,
                profile=profile,
                excluded_movie_ids=excluded_movie_ids | seen_movie_ids,
                limit=limit - len(candidates),
                subscribed_only=subscribed_only,
                build_id=active_build.id,
            )
        )
    return candidates


def build_profile_feature_rows(profile: UserProfile) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    rows.extend(top_feature_rows("genre", profile.genre_scores, limit=PROFILE_FEATURE_LIMITS["genre"]))
    rows.extend(top_feature_rows("keyword", profile.keyword_scores, limit=PROFILE_FEATURE_LIMITS["keyword"]))
    rows.extend(top_feature_rows("actor", profile.actor_scores, limit=PROFILE_FEATURE_LIMITS["actor"]))
    rows.extend(top_feature_rows("director", profile.director_scores, limit=PROFILE_FEATURE_LIMITS["director"]))
    rows.extend(top_feature_rows("theme", profile.theme_scores, limit=PROFILE_FEATURE_LIMITS["theme"]))
    rows.extend(top_feature_rows("mood", profile.mood_scores, limit=PROFILE_FEATURE_LIMITS["mood"]))
    return rows


def top_feature_rows(node_type: str, scores: dict, *, limit: int) -> list[tuple[str, str, float]]:
    return [
        (node_type, str(key), float(score))
        for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        if score > 0
    ]


def generate_graph_candidates(
    db: Session,
    *,
    build_id: int,
    feature_rows: list[tuple[str, str, float]],
    profile: UserProfile,
    excluded_movie_ids: set[int],
    limit: int,
    subscribed_only: bool = False,
) -> list[CandidateScore]:
    values_sql, params = build_feature_values_sql(feature_rows)
    excluded_ids = list(excluded_movie_ids) or [-1]
    subscribed_ott_ids = list(profile.subscribed_ott_ids) or [-1]
    candidate_limit = (
        limit * NEGATIVE_CANDIDATE_OVERSAMPLE_FACTOR
        if profile.negative_movie_ids
        else limit
    )
    subscribed_filter = """
              AND EXISTS (
                  SELECT 1
                  FROM movie_otts required_ott
                  WHERE required_ott.movie_id = matched_movie_scores.movie_id
                    AND required_ott.ott_id = ANY(:subscribed_ott_ids)
                    AND required_ott.is_streaming IS TRUE
              )
    """ if subscribed_only and profile.subscribed_ott_ids else ""
    rows = db.execute(
        text(
            f"""
            WITH profile_feature(node_type, ref_id, profile_score) AS (
                VALUES {values_sql}
            ),
            profile_feature_nodes AS MATERIALIZED (
                SELECT
                    feature_node.id AS feature_node_id,
                    profile_feature.node_type,
                    profile_feature.ref_id,
                    profile_feature.profile_score
                FROM profile_feature
                JOIN ontology_nodes feature_node
                    ON feature_node.build_id = :build_id
                    AND feature_node.node_type = profile_feature.node_type
                    AND feature_node.ref_id = profile_feature.ref_id
            ),
            quality_movies AS MATERIALIZED (
                SELECT
                    movie.id AS movie_id,
                    movie.popularity,
                    movie.vote_average
                FROM movies movie
                WHERE movie.id <> ALL(:excluded_movie_ids)
                  AND {movie_basic_quality_filter_sql("movie")}
            ),
            eligible_movies AS MATERIALIZED (
                SELECT
                    movie_node.id AS movie_node_id,
                    quality_movie.movie_id,
                    quality_movie.popularity,
                    quality_movie.vote_average
                FROM quality_movies quality_movie
                JOIN ontology_nodes movie_node
                    ON movie_node.build_id = :build_id
                    AND movie_node.node_type = 'movie'
                    AND movie_node.ref_id = quality_movie.movie_id::text
                WHERE EXISTS (
                    SELECT 1
                    FROM movie_genres movie_genre
                    WHERE movie_genre.movie_id = quality_movie.movie_id
                )
                  AND (
                    SELECT count(DISTINCT movie_genre.genre_id)
                    FROM movie_genres movie_genre
                    WHERE movie_genre.movie_id = quality_movie.movie_id
                  ) <= :max_genre_count
            ),
            matched_profile_edges AS (
                SELECT
                    edge.source_node_id AS movie_node_id,
                    profile_feature_node.node_type,
                    edge.weight * edge.confidence * profile_feature_node.profile_score
                        AS contribution
                FROM profile_feature_nodes profile_feature_node
                JOIN ontology_edges edge
                    ON edge.build_id = :build_id
                    AND edge.target_node_id = profile_feature_node.feature_node_id
                    AND edge.relation_type IN (
                        'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                    )
                OFFSET 0
            ),
            matched_type_aggregates AS (
                SELECT
                    eligible_movie.movie_id,
                    max(eligible_movie.popularity) AS popularity,
                    max(eligible_movie.vote_average) AS vote_average,
                    matched_edge.node_type,
                    sum(matched_edge.contribution) AS type_raw_score,
                    max(matched_edge.contribution) AS type_peak_score
                FROM matched_profile_edges matched_edge
                JOIN eligible_movies eligible_movie
                    ON eligible_movie.movie_node_id = matched_edge.movie_node_id
                GROUP BY eligible_movie.movie_id, matched_edge.node_type
            ),
            matched_type_scores AS (
                SELECT
                    movie_id,
                    popularity,
                    vote_average,
                    node_type,
                    type_raw_score,
                    type_peak_score,
                    CASE
                        WHEN type_peak_score > 0 THEN
                            type_peak_score * (
                                1.0 + ln(greatest(type_raw_score / type_peak_score, 1.0))
                            )
                        ELSE 0.0
                    END AS type_score
                FROM matched_type_aggregates
            ),
            matched_movie_scores AS (
                SELECT
                    movie_id,
                    sum(type_raw_score) AS graph_raw_score,
                    sum(type_score) AS graph_score,
                    max(popularity) AS popularity,
                    max(vote_average) AS vote_average
                FROM matched_type_scores
                GROUP BY movie_id
            ),
            top_movie_scores AS MATERIALIZED (
                SELECT
                    matched_movie_scores.movie_id,
                    matched_movie_scores.graph_raw_score,
                    matched_movie_scores.graph_score,
                    matched_movie_scores.popularity,
                    matched_movie_scores.vote_average,
                    EXISTS (
                        SELECT 1
                        FROM movie_otts movie_ott
                        WHERE movie_ott.movie_id = matched_movie_scores.movie_id
                          AND movie_ott.ott_id = ANY(:subscribed_ott_ids)
                          AND movie_ott.is_streaming IS TRUE
                    ) AS is_subscribed
                FROM matched_movie_scores
                WHERE true
                {subscribed_filter}
                ORDER BY
                    round(matched_movie_scores.graph_score::numeric, 12) DESC,
                    matched_movie_scores.popularity DESC NULLS LAST,
                    matched_movie_scores.movie_id ASC
                LIMIT :candidate_limit
            )
            SELECT
                top_movie.movie_id,
                top_movie.graph_raw_score,
                top_movie.graph_score,
                top_movie.popularity,
                top_movie.vote_average,
                top_movie.is_subscribed
            FROM top_movie_scores top_movie
            ORDER BY
                round(top_movie.graph_score::numeric, 12) DESC,
                top_movie.popularity DESC NULLS LAST,
                top_movie.movie_id ASC
            """
        ),
        {
            **params,
            "build_id": build_id,
            "excluded_movie_ids": excluded_ids,
            "subscribed_ott_ids": subscribed_ott_ids,
            "candidate_limit": candidate_limit,
            **candidate_quality_params(),
        },
    ).all()
    feature_details = load_candidate_feature_details(
        db,
        build_id=build_id,
        feature_rows=feature_rows,
        movie_ids=[int(row.movie_id) for row in rows],
    )
    negative_matches = load_negative_feature_matches(
        db,
        build_id=build_id,
        movie_ids=[int(row.movie_id) for row in rows],
        negative_movie_ids=profile.negative_movie_ids,
    )
    candidates: list[CandidateScore] = []
    for row in rows:
        feature_detail = feature_details.get(int(row.movie_id), {})
        negative_match = negative_matches.get(int(row.movie_id), {})
        matched_features: dict[str, float] = {}
        for node_type in GRAPH_FEATURE_TYPES:
            matched_features.update(
                (feature_detail.get("matched_features_by_type") or {}).get(node_type, {}) or {}
            )
        popularity_score = normalize_popularity(row.popularity)
        rating_score = normalize_rating(row.vote_average)
        ott_bonus = SUBSCRIBED_OTT_SCORE_BONUS if row.is_subscribed else 0.0
        graph_raw_score = float(row.graph_raw_score or 0.0)
        graph_score = float(row.graph_score or 0.0)
        graph_type_scores = {
            node_type: float((feature_detail.get("graph_type_scores") or {}).get(node_type, 0.0))
            for node_type in GRAPH_FEATURE_TYPES
        }
        negative_raw_score = float(negative_match.get("negative_raw_score", 0.0))
        negative_penalty = capped_negative_penalty(graph_score, negative_raw_score)
        score = graph_score - negative_penalty + popularity_score + rating_score + ott_bonus
        candidates.append(
            CandidateScore(
                movie_id=int(row.movie_id),
                score=score,
                source="ontology",
                source_scores={
                    "graph": graph_score,
                    "graph_raw": graph_raw_score,
                    **{
                        f"graph_{node_type}": score
                        for node_type, score in graph_type_scores.items()
                    },
                    "negative_raw": negative_raw_score,
                    "negative_penalty": -negative_penalty,
                    "popularity": popularity_score,
                    "rating": rating_score,
                    "ott_bonus": ott_bonus,
                },
                explanation_tags=list(matched_features.keys())[:8],
                metadata={
                    "matched_feature_count": int(feature_detail.get("matched_feature_count", 0)),
                    "matched_feature_counts": {
                        node_type: int(
                            (feature_detail.get("matched_feature_counts") or {}).get(node_type, 0)
                        )
                        for node_type in GRAPH_FEATURE_TYPES
                    },
                    "graph_type_raw_scores": {
                        node_type: float(
                            (feature_detail.get("graph_type_raw_scores") or {}).get(node_type, 0.0)
                        )
                        for node_type in GRAPH_FEATURE_TYPES
                    },
                    "negative_feature_count": int(negative_match.get("negative_feature_count", 0)),
                    "negative_explanation_tags": negative_match.get("negative_explanation_tags", []),
                    "is_subscribed": bool(row.is_subscribed),
                    "profile_type": profile.profile_type,
                },
            )
        )
    return sorted(
        candidates,
        key=lambda candidate: (-round(candidate.score, 12), candidate.movie_id),
    )[:limit]


def load_candidate_feature_details(
    db: Session,
    *,
    build_id: int,
    feature_rows: list[tuple[str, str, float]],
    movie_ids: list[int],
) -> dict[int, dict]:
    if not feature_rows or not movie_ids:
        return {}

    values_sql, params = build_feature_values_sql(feature_rows)
    rows = db.execute(
        text(
            f"""
            WITH profile_feature(node_type, ref_id, profile_score) AS (
                VALUES {values_sql}
            ),
            selected_movie_nodes AS MATERIALIZED (
                SELECT
                    movie_node.id AS movie_node_id,
                    movie_node.ref_id::integer AS movie_id
                FROM ontology_nodes movie_node
                WHERE movie_node.build_id = :build_id
                  AND movie_node.node_type = 'movie'
                  AND movie_node.ref_id = ANY(:movie_ids)
            ),
            selected_movie_edges AS MATERIALIZED (
                SELECT
                    selected_movie.movie_id,
                    edge.target_node_id,
                    edge.weight,
                    edge.confidence
                FROM selected_movie_nodes selected_movie
                JOIN ontology_edges edge
                    ON edge.build_id = :build_id
                    AND edge.source_node_id = selected_movie.movie_node_id
                    AND edge.relation_type IN (
                        'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                    )
            ),
            selected_type_aggregates AS (
                SELECT
                    selected_edge.movie_id,
                    profile_feature.node_type,
                    sum(
                        selected_edge.weight
                        * selected_edge.confidence
                        * profile_feature.profile_score
                    ) AS type_raw_score,
                    max(
                        selected_edge.weight
                        * selected_edge.confidence
                        * profile_feature.profile_score
                    ) AS type_peak_score,
                    count(DISTINCT feature_node.id) AS matched_feature_count,
                    json_object_agg(
                        profile_feature.node_type || ':' || profile_feature.ref_id,
                        selected_edge.weight
                        * selected_edge.confidence
                        * profile_feature.profile_score
                        ORDER BY
                            selected_edge.weight
                            * selected_edge.confidence
                            * profile_feature.profile_score DESC
                    ) AS matched_features
                FROM selected_movie_edges selected_edge
                JOIN ontology_nodes feature_node
                    ON feature_node.id = selected_edge.target_node_id
                    AND feature_node.build_id = :build_id
                JOIN profile_feature
                    ON profile_feature.node_type = feature_node.node_type
                    AND profile_feature.ref_id = feature_node.ref_id
                GROUP BY selected_edge.movie_id, profile_feature.node_type
            ),
            selected_type_scores AS (
                SELECT
                    movie_id,
                    node_type,
                    type_raw_score,
                    matched_feature_count,
                    matched_features,
                    CASE
                        WHEN type_peak_score > 0 THEN
                            type_peak_score * (
                                1.0 + ln(greatest(type_raw_score / type_peak_score, 1.0))
                            )
                        ELSE 0.0
                    END AS type_score
                FROM selected_type_aggregates
            )
            SELECT
                movie_id,
                json_object_agg(node_type, type_score) AS graph_type_scores,
                json_object_agg(node_type, type_raw_score) AS graph_type_raw_scores,
                sum(matched_feature_count) AS matched_feature_count,
                json_object_agg(node_type, matched_feature_count) AS matched_feature_counts,
                json_object_agg(node_type, matched_features) AS matched_features_by_type
            FROM selected_type_scores
            GROUP BY movie_id
            """
        ),
        {
            **params,
            "build_id": build_id,
            "movie_ids": [str(movie_id) for movie_id in movie_ids],
        },
    )
    return {
        int(row.movie_id): {
            "graph_type_scores": row.graph_type_scores or {},
            "graph_type_raw_scores": row.graph_type_raw_scores or {},
            "matched_feature_count": int(row.matched_feature_count or 0),
            "matched_feature_counts": row.matched_feature_counts or {},
            "matched_features_by_type": row.matched_features_by_type or {},
        }
        for row in rows
    }


def build_feature_values_sql(feature_rows: list[tuple[str, str, float]]) -> tuple[str, dict]:
    values: list[str] = []
    params: dict = {}
    for index, (node_type, ref_id, score) in enumerate(feature_rows):
        values.append(f"(:node_type_{index}, :ref_id_{index}, :score_{index})")
        params[f"node_type_{index}"] = node_type
        params[f"ref_id_{index}"] = ref_id
        params[f"score_{index}"] = score
    return ", ".join(values), params


def generate_fallback_candidates(
    db: Session,
    *,
    profile: UserProfile,
    excluded_movie_ids: set[int],
    limit: int,
    subscribed_only: bool = False,
    build_id: int | None = None,
) -> list[CandidateScore]:
    if limit <= 0:
        return []
    stmt = (
        select(Movie)
        .outerjoin(movie_genres, movie_genres.c.movie_id == Movie.id)
        .where(candidate_quality_clause(Movie))
        .group_by(Movie.id)
        .having(func.count(func.distinct(movie_genres.c.genre_id)) >= 1)
        .having(func.count(func.distinct(movie_genres.c.genre_id)) <= MAX_CANDIDATE_GENRE_COUNT)
        .order_by(desc(Movie.popularity), desc(Movie.vote_average), Movie.id)
        .limit(limit)
    )
    if excluded_movie_ids:
        stmt = stmt.where(Movie.id.not_in(excluded_movie_ids))
    if subscribed_only and profile.subscribed_ott_ids:
        from app.models.mapping import MovieOtt

        stmt = (
            stmt.join(MovieOtt, MovieOtt.movie_id == Movie.id)
            .where(MovieOtt.ott_id.in_(profile.subscribed_ott_ids))
            .where(MovieOtt.is_streaming.is_(True))
        )
    movies = list(db.scalars(stmt))
    negative_matches = load_negative_feature_matches(
        db,
        build_id=build_id,
        movie_ids=[movie.id for movie in movies],
        negative_movie_ids=profile.negative_movie_ids,
    )
    candidates: list[CandidateScore] = []
    for movie in movies:
        negative_match = negative_matches.get(movie.id, {})
        popularity_score = normalize_popularity(movie.popularity)
        rating_score = normalize_rating(movie.vote_average)
        base_score = popularity_score + rating_score
        negative_raw_score = float(negative_match.get("negative_raw_score", 0.0))
        negative_penalty = capped_negative_penalty(
            base_score,
            negative_raw_score * FALLBACK_NEGATIVE_PENALTY_SCALE,
        )
        candidates.append(
            CandidateScore(
                movie_id=movie.id,
                score=base_score - negative_penalty,
                source="fallback",
                source_scores={
                    "popularity": popularity_score,
                    "rating": rating_score,
                    "negative_raw": negative_raw_score,
                    "negative_penalty": -negative_penalty,
                },
                explanation_tags=["popular"],
                metadata={
                    "profile_type": profile.profile_type,
                    "negative_feature_count": int(negative_match.get("negative_feature_count", 0)),
                    "negative_explanation_tags": negative_match.get("negative_explanation_tags", []),
                },
            )
        )
    return candidates


def normalize_popularity(value: float | None) -> float:
    if not value:
        return 0.0
    return min(float(value) / POPULARITY_NORMALIZATION_DIVISOR, MAX_NORMALIZED_POPULARITY)


def normalize_rating(value: float | None) -> float:
    if not value:
        return 0.0
    return max(float(value), 0.0) / RATING_NORMALIZATION_DIVISOR


def capped_negative_penalty(base_score: float, negative_raw_score: float) -> float:
    return min(max(float(negative_raw_score), 0.0), abs(float(base_score)) * MAX_NEGATIVE_PENALTY_RATIO)


def negative_penalty_params() -> dict:
    return {
        "passed_weight": abs(PASSED_ACTION_WEIGHT) / PASSED_FEATURE_SIGNAL_NORMALIZER,
        **{
            f"negative_{node_type}_weight": weight
            for node_type, weight in NEGATIVE_FEATURE_TYPE_WEIGHTS.items()
        },
    }


def load_negative_feature_matches(
    db: Session,
    *,
    build_id: int | None,
    movie_ids: list[int],
    negative_movie_ids: set[int],
) -> dict[int, dict]:
    if build_id is None or not movie_ids or not negative_movie_ids:
        return {}
    rows = db.execute(
        text(
            """
            WITH negative_profile_feature AS (
                SELECT
                    negative_feature_node.node_type,
                    negative_feature_node.ref_id,
                    sum(
                        negative_edge.weight
                        * negative_edge.confidence
                        * :passed_weight
                        * CASE negative_feature_node.node_type
                            WHEN 'genre' THEN :negative_genre_weight
                            WHEN 'keyword' THEN :negative_keyword_weight
                            WHEN 'actor' THEN :negative_actor_weight
                            WHEN 'director' THEN :negative_director_weight
                            WHEN 'theme' THEN :negative_theme_weight
                            WHEN 'mood' THEN :negative_mood_weight
                            ELSE 0.0
                        END
                    ) AS negative_profile_score
                FROM ontology_nodes negative_movie_node
                JOIN ontology_edges negative_edge
                    ON negative_edge.build_id = :build_id
                    AND negative_edge.source_node_id = negative_movie_node.id
                    AND negative_edge.relation_type IN (
                        'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                    )
                JOIN ontology_nodes negative_feature_node
                    ON negative_feature_node.id = negative_edge.target_node_id
                WHERE negative_movie_node.build_id = :build_id
                  AND negative_movie_node.node_type = 'movie'
                  AND negative_movie_node.ref_id = ANY(:negative_movie_ids)
                GROUP BY negative_feature_node.node_type, negative_feature_node.ref_id
            )
            SELECT
                movie_node.ref_id::integer AS movie_id,
                sum(
                    edge.weight
                    * edge.confidence
                    * negative_profile_feature.negative_profile_score
                ) AS negative_raw_score,
                count(DISTINCT feature_node.id) AS negative_feature_count,
                json_object_agg(
                    negative_profile_feature.node_type || ':' || negative_profile_feature.ref_id,
                    edge.weight
                    * edge.confidence
                    * negative_profile_feature.negative_profile_score
                ) AS negative_features
            FROM ontology_nodes movie_node
            JOIN ontology_edges edge
                ON edge.build_id = :build_id
                AND edge.source_node_id = movie_node.id
                AND edge.relation_type IN (
                    'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                )
            JOIN ontology_nodes feature_node
                ON feature_node.id = edge.target_node_id
            JOIN negative_profile_feature
                ON negative_profile_feature.node_type = feature_node.node_type
                AND negative_profile_feature.ref_id = feature_node.ref_id
            WHERE movie_node.build_id = :build_id
              AND movie_node.node_type = 'movie'
              AND movie_node.ref_id = ANY(:movie_ids)
            GROUP BY movie_node.ref_id
            """
        ),
        {
            "build_id": build_id,
            "movie_ids": [str(movie_id) for movie_id in movie_ids],
            "negative_movie_ids": [str(movie_id) for movie_id in negative_movie_ids],
            **negative_penalty_params(),
        },
    )
    return {
        int(row.movie_id): {
            "negative_raw_score": float(row.negative_raw_score or 0.0),
            "negative_feature_count": int(row.negative_feature_count or 0),
            "negative_explanation_tags": list((row.negative_features or {}).keys())[:8],
        }
        for row in rows
    }


def candidate_quality_params() -> dict:
    return {
        "allowed_statuses": list(ALLOWED_CANDIDATE_STATUSES),
        "min_vote_count": MIN_CANDIDATE_VOTE_COUNT,
        "min_popularity": MIN_CANDIDATE_POPULARITY,
        "min_runtime": MIN_CANDIDATE_RUNTIME,
        "max_genre_count": MAX_CANDIDATE_GENRE_COUNT,
    }


def movie_basic_quality_filter_sql(alias: str) -> str:
    return f"""
                  {alias}.adult IS FALSE
                  AND {alias}.status = ANY(:allowed_statuses)
                  AND COALESCE({alias}.runtime, 0) >= :min_runtime
                  AND COALESCE({alias}.vote_count, 0) >= :min_vote_count
                  AND COALESCE({alias}.popularity, 0) > :min_popularity
    """


def candidate_quality_clause(movie_model) -> object:
    return (
        movie_model.adult.is_(False)
        & movie_model.status.in_(ALLOWED_CANDIDATE_STATUSES)
        & (func.coalesce(movie_model.runtime, 0) >= MIN_CANDIDATE_RUNTIME)
        & (func.coalesce(movie_model.vote_count, 0) >= MIN_CANDIDATE_VOTE_COUNT)
        & (func.coalesce(movie_model.popularity, 0) > MIN_CANDIDATE_POPULARITY)
    )
