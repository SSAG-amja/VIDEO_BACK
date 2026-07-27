from sqlalchemy.orm import Session
from sqlalchemy import desc, select, text

from app.crud.recsys.ontology import get_active_build
from app.services.recsys.v2.config import DEFAULT_CANDIDATE_SLICE_SIZE
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile, UserProfile
from app.models.movie import Movie


def generate_candidates(
    db: Session,
    *,
    profile: UserProfile,
    session_profile: SessionProfile,
    limit: int = DEFAULT_CANDIDATE_SLICE_SIZE,
    subscribed_only: bool = False,
) -> list[CandidateScore]:
    active_build = get_active_build(db)
    excluded_movie_ids = profile.excluded_movie_ids | session_profile.recent_skipped_movie_ids
    if active_build is None:
        return generate_fallback_candidates(
            db,
            profile=profile,
            excluded_movie_ids=excluded_movie_ids,
            limit=limit,
            subscribed_only=subscribed_only,
        )

    feature_rows = build_profile_feature_rows(profile)
    if not feature_rows:
        return generate_fallback_candidates(
            db,
            profile=profile,
            excluded_movie_ids=excluded_movie_ids,
            limit=limit,
            subscribed_only=subscribed_only,
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
            )
        )
    return candidates


def build_profile_feature_rows(profile: UserProfile) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    rows.extend(top_feature_rows("genre", profile.genre_scores, limit=20))
    rows.extend(top_feature_rows("keyword", profile.keyword_scores, limit=80))
    rows.extend(top_feature_rows("actor", profile.actor_scores, limit=80))
    rows.extend(top_feature_rows("director", profile.director_scores, limit=40))
    rows.extend(top_feature_rows("theme", profile.theme_scores, limit=40))
    rows.extend(top_feature_rows("mood", profile.mood_scores, limit=24))
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
    subscribed_filter = """
              AND EXISTS (
                  SELECT 1
                  FROM movie_otts required_ott
                  WHERE required_ott.movie_id = matched_movies.movie_id
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
            matched_movies AS (
                SELECT
                    movie_node.ref_id::integer AS movie_id,
                    sum(edge.weight * edge.confidence * profile_feature.profile_score) AS graph_score,
                    max(movie.popularity) AS popularity,
                    max(movie.vote_average) AS vote_average,
                    count(DISTINCT feature_node.id) AS matched_feature_count,
                    json_object_agg(
                        profile_feature.node_type || ':' || profile_feature.ref_id,
                        edge.weight * edge.confidence * profile_feature.profile_score
                    ) AS matched_features
                FROM profile_feature
                JOIN ontology_nodes feature_node
                    ON feature_node.build_id = :build_id
                    AND feature_node.node_type = profile_feature.node_type
                    AND feature_node.ref_id = profile_feature.ref_id
                JOIN ontology_edges edge
                    ON edge.build_id = :build_id
                    AND edge.target_node_id = feature_node.id
                    AND edge.relation_type IN (
                        'has_genre', 'has_keyword', 'has_actor', 'has_director', 'has_theme', 'has_mood'
                    )
                JOIN ontology_nodes movie_node
                    ON movie_node.id = edge.source_node_id
                    AND movie_node.node_type = 'movie'
                JOIN movies movie
                    ON movie.id = movie_node.ref_id::integer
                WHERE movie.adult IS FALSE
                  AND movie.id <> ALL(:excluded_movie_ids)
                GROUP BY movie_node.ref_id
            )
            SELECT
                matched_movies.movie_id,
                matched_movies.graph_score,
                matched_movies.popularity,
                matched_movies.vote_average,
                matched_movies.matched_feature_count,
                matched_movies.matched_features,
                EXISTS (
                    SELECT 1
                    FROM movie_otts movie_ott
                    WHERE movie_ott.movie_id = matched_movies.movie_id
                      AND movie_ott.ott_id = ANY(:subscribed_ott_ids)
                      AND movie_ott.is_streaming IS TRUE
                ) AS is_subscribed
            FROM matched_movies
            WHERE true
            {subscribed_filter}
            ORDER BY matched_movies.graph_score DESC, matched_movies.popularity DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {
            **params,
            "build_id": build_id,
            "excluded_movie_ids": excluded_ids,
            "subscribed_ott_ids": subscribed_ott_ids,
            "limit": limit,
        },
    )
    candidates: list[CandidateScore] = []
    for row in rows:
        popularity_score = normalize_popularity(row.popularity)
        rating_score = normalize_rating(row.vote_average)
        ott_bonus = 0.15 if row.is_subscribed else 0.0
        score = float(row.graph_score or 0.0) + popularity_score + rating_score + ott_bonus
        candidates.append(
            CandidateScore(
                movie_id=int(row.movie_id),
                score=score,
                source="ontology",
                source_scores={
                    "graph": float(row.graph_score or 0.0),
                    "popularity": popularity_score,
                    "rating": rating_score,
                    "ott_bonus": ott_bonus,
                },
                explanation_tags=list((row.matched_features or {}).keys())[:8],
                metadata={
                    "matched_feature_count": int(row.matched_feature_count or 0),
                    "is_subscribed": bool(row.is_subscribed),
                    "profile_type": profile.profile_type,
                },
            )
        )
    return candidates


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
) -> list[CandidateScore]:
    if limit <= 0:
        return []
    stmt = (
        select(Movie)
        .where(Movie.adult.is_(False))
        .order_by(desc(Movie.popularity), desc(Movie.vote_average))
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
    return [
        CandidateScore(
            movie_id=movie.id,
            score=normalize_popularity(movie.popularity) + normalize_rating(movie.vote_average),
            source="fallback",
            source_scores={
                "popularity": normalize_popularity(movie.popularity),
                "rating": normalize_rating(movie.vote_average),
            },
            explanation_tags=["popular"],
            metadata={"profile_type": profile.profile_type},
        )
        for movie in movies
    ]


def normalize_popularity(value: float | None) -> float:
    if not value:
        return 0.0
    return min(float(value) / 100.0, 2.0)


def normalize_rating(value: float | None) -> float:
    if not value:
        return 0.0
    return max(float(value), 0.0) / 10.0
