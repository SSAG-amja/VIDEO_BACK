import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud.recsys.movies import load_streaming_movie_ids
from app.db.session import SessionLocal
from app.services.recsys.v2.candidate_generator import generate_candidates
from app.services.recsys.v2.dynamic_reranker import rerank_for_session
from app.services.recsys.v2.post_processor import apply_safety_filters
from app.services.recsys.v2.ranker import rank_candidates
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile, UserProfile
from app.services.recsys.v2.scorer import score_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ontology recommender from JSON input.")
    parser.add_argument("--input", required=True, help="Input JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--limit", type=int, default=None, help="Override output candidate count")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    limit = int(args.limit or payload.get("limit") or 100)

    db = SessionLocal()
    try:
        result = run_recommendation(db, payload=payload, limit=limit)
    finally:
        db.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {output_path} count={result['count']}")


def run_recommendation(db: Session, *, payload: dict[str, Any], limit: int) -> dict[str, Any]:
    profile = build_profile_from_payload(db, payload)
    session_profile = SessionProfile(feed_session_key="ontol_test")
    candidate_limit = max(limit, int(payload.get("candidate_limit") or limit))
    subscribed_only = bool(payload.get("subscribed_only", False))

    candidates = generate_candidates(
        db,
        profile=profile,
        session_profile=session_profile,
        limit=candidate_limit,
        subscribed_only=subscribed_only,
    )
    scored = score_candidates(candidates, profile=profile, session_profile=session_profile)
    ranked = rank_candidates(scored)
    reranked = rerank_for_session(ranked, session_profile=session_profile)
    filtered = apply_safety_filters(reranked, profile=profile)
    if subscribed_only:
        filtered = apply_subscribed_only_filter(db, filtered, subscribed_ott_ids=profile.subscribed_ott_ids)
    selected = filtered[:limit]

    return {
        "input_summary": {
            "preferred_genre_ids": sorted(profile.genre_scores.keys()),
            "favorite_movie_ids": sorted(profile.favorite_movie_ids),
            "subscribed_ott_ids": sorted(profile.subscribed_ott_ids),
            "excluded_movie_ids": sorted(profile.excluded_movie_ids),
            "profile_type": profile.profile_type,
        },
        "count": len(selected),
        "items": [candidate_to_dict(index, candidate) for index, candidate in enumerate(selected, start=1)],
    }


def build_profile_from_payload(db: Session, payload: dict[str, Any]) -> UserProfile:
    favorite_movie_ids = to_int_set(payload.get("favorite_movie_ids")) | to_int_set(payload.get("saved_movie_ids"))
    pinned_movie_ids = to_int_set(payload.get("pinned_movie_ids"))
    watched_movie_ids = to_int_set(payload.get("watched_movie_ids"))
    passed_movie_ids = to_int_set(payload.get("passed_movie_ids"))
    preferred_genre_ids = to_int_set(payload.get("preferred_genre_ids"))
    subscribed_ott_ids = to_int_set(payload.get("subscribed_ott_ids"))

    positive_movie_ids = favorite_movie_ids | pinned_movie_ids | watched_movie_ids
    interaction_count = len(pinned_movie_ids | watched_movie_ids | passed_movie_ids)
    profile = UserProfile(
        user_id=int(payload.get("user_id") or 0),
        profile_type=profile_type(
            has_profile=bool(positive_movie_ids or preferred_genre_ids or subscribed_ott_ids),
            interaction_count=interaction_count,
        ),
        favorite_movie_ids=favorite_movie_ids,
        subscribed_ott_ids=subscribed_ott_ids,
        excluded_movie_ids=watched_movie_ids | passed_movie_ids,
        negative_movie_ids=passed_movie_ids,
    )

    add_scores(profile.genre_scores, preferred_genre_ids, 4.0)
    add_movie_feature_scores(db, profile, favorite_movie_ids, genre=2.5, keyword=1.8, actor=1.0, director=1.5)
    add_movie_feature_scores(db, profile, pinned_movie_ids, genre=3.0, keyword=2.2, actor=1.2, director=1.8)
    add_movie_feature_scores(db, profile, watched_movie_ids, genre=1.0, keyword=0.8, actor=0.4, director=0.6)

    theme_scores, mood_scores = load_movie_semantic_scores(db, positive_movie_ids)
    profile.theme_scores.update(theme_scores)
    profile.mood_scores.update(mood_scores)
    return profile


def add_movie_feature_scores(
    db: Session,
    profile: UserProfile,
    movie_ids: set[int],
    *,
    genre: float,
    keyword: float,
    actor: float,
    director: float,
) -> None:
    if not movie_ids:
        return
    add_scores(profile.genre_scores, load_feature_ids(db, "movie_genres", "genre_id", movie_ids), genre)
    add_scores(profile.keyword_scores, load_feature_ids(db, "movie_keywords", "keyword_id", movie_ids), keyword)
    add_scores(profile.actor_scores, load_feature_ids(db, "movie_actors", "actor_id", movie_ids), actor)
    add_scores(profile.director_scores, load_feature_ids(db, "movie_directors", "director_id", movie_ids), director)


def load_feature_ids(db: Session, table_name: str, column_name: str, movie_ids: set[int]) -> set[int]:
    rows = db.execute(
        text(
            f"""
            SELECT DISTINCT {column_name}
            FROM {table_name}
            WHERE movie_id = ANY(:movie_ids)
            """
        ),
        {"movie_ids": list(movie_ids)},
    )
    return {int(row[0]) for row in rows if row[0] is not None}


def load_movie_semantic_scores(db: Session, movie_ids: set[int]) -> tuple[dict[str, float], dict[str, float]]:
    if not movie_ids:
        return {}, {}
    row = db.execute(
        text("SELECT id FROM ontology_builds WHERE is_active IS TRUE AND status = 'success' ORDER BY id DESC LIMIT 1")
    ).first()
    if not row:
        return {}, {}
    build_id = int(row[0])
    rows = db.execute(
        text(
            """
            SELECT target_node.node_type, target_node.ref_id, sum(edge.weight * edge.confidence) AS score
            FROM ontology_nodes movie_node
            JOIN ontology_edges edge
                ON edge.build_id = :build_id
                AND edge.source_node_id = movie_node.id
                AND edge.relation_type IN ('has_theme', 'has_mood')
            JOIN ontology_nodes target_node
                ON target_node.id = edge.target_node_id
            WHERE movie_node.build_id = :build_id
              AND movie_node.node_type = 'movie'
              AND movie_node.ref_id = ANY(:movie_ids)
            GROUP BY target_node.node_type, target_node.ref_id
            """
        ),
        {"build_id": build_id, "movie_ids": [str(movie_id) for movie_id in movie_ids]},
    )
    theme_scores: dict[str, float] = {}
    mood_scores: dict[str, float] = {}
    for node_type, ref_id, score in rows:
        if node_type == "theme":
            theme_scores[str(ref_id)] = float(score or 0.0)
        elif node_type == "mood":
            mood_scores[str(ref_id)] = float(score or 0.0)
    return theme_scores, mood_scores


def apply_subscribed_only_filter(
    db: Session,
    candidates: list[CandidateScore],
    *,
    subscribed_ott_ids: set[int],
) -> list[CandidateScore]:
    if not candidates or not subscribed_ott_ids:
        return []
    movie_ids = [candidate.movie_id for candidate in candidates]
    streaming_movie_ids = load_streaming_movie_ids(db, list(subscribed_ott_ids), movie_ids)
    return [candidate for candidate in candidates if candidate.movie_id in streaming_movie_ids]


def add_scores(target: dict, keys: set[int], value: float) -> None:
    for key in keys:
        target[key] = target.get(key, 0.0) + value


def profile_type(*, has_profile: bool, interaction_count: int) -> str:
    if not has_profile:
        return "no-profile"
    if interaction_count == 0:
        return "onboarding-only"
    if interaction_count < 5:
        return "sparse-profile"
    if interaction_count < 20:
        return "light-profile"
    return "established-profile"


def to_int_set(value: Any) -> set[int]:
    if not value:
        return set()
    return {int(item) for item in value}


def candidate_to_dict(rank: int, candidate: CandidateScore) -> dict[str, Any]:
    return {
        "rank": rank,
        "movie_id": candidate.movie_id,
        "score": candidate.score,
        "source": candidate.source,
        "source_scores": candidate.source_scores,
        "explanation_tags": candidate.explanation_tags,
        "metadata": candidate.metadata,
    }


if __name__ == "__main__":
    main()
