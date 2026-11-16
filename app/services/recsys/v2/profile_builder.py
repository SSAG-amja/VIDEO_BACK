from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.crud.recsys.ontology import get_active_build
from app.crud.recsys.onboarding import (
    load_movie_actor_ids,
    load_movie_director_ids,
    load_movie_genre_ids,
    load_movie_keyword_ids,
    load_user_favorite_movie_ids,
    load_user_genre_ids,
    load_user_ott_ids,
)
from app.models.mapping import PlaylistMovie, UserInteraction
from app.models.playlist import Playlist
from app.services.recsys.v2.config import (
    PREFERRED_GENRE_WEIGHT,
    PROFILE_ACTION_FEATURE_WEIGHTS,
)
from app.services.recsys.v2.schemas import UserProfile


def add_scores(target: dict, keys: list | set, value: float) -> None:
    for key in keys:
        target[key] = target.get(key, 0.0) + value


def build_user_profile(db: Session, user_id: int) -> UserProfile:
    favorite_movie_ids = set(load_user_favorite_movie_ids(db, user_id))
    saved_movie_ids = load_saved_movie_ids(db, user_id)
    onboarding_genre_ids = set(load_user_genre_ids(db, user_id))
    subscribed_ott_ids = set(load_user_ott_ids(db, user_id))
    pinned_movie_ids, watched_movie_ids, passed_movie_ids = load_interaction_movie_sets(db, user_id)

    positive_movie_ids = favorite_movie_ids | saved_movie_ids | pinned_movie_ids | watched_movie_ids
    profile = UserProfile(
        user_id=user_id,
        profile_type="no-profile",
        favorite_movie_ids=favorite_movie_ids,
        saved_movie_ids=saved_movie_ids,
        subscribed_ott_ids=subscribed_ott_ids,
        excluded_movie_ids=watched_movie_ids | passed_movie_ids,
        negative_movie_ids=passed_movie_ids,
    )

    add_scores(profile.genre_scores, onboarding_genre_ids, PREFERRED_GENRE_WEIGHT)
    add_movie_feature_scores(db, profile, favorite_movie_ids, action="favorite")
    add_movie_feature_scores(db, profile, saved_movie_ids, action="saved")
    add_movie_feature_scores(db, profile, pinned_movie_ids, action="pinned")
    add_movie_feature_scores(db, profile, watched_movie_ids, action="watched")

    theme_scores, mood_scores = load_movie_semantic_scores(db, movie_ids=positive_movie_ids)
    add_scores(profile.theme_scores, theme_scores.keys(), 0.0)
    add_scores(profile.mood_scores, mood_scores.keys(), 0.0)
    profile.theme_scores.update(theme_scores)
    profile.mood_scores.update(mood_scores)

    interaction_count = len(saved_movie_ids | pinned_movie_ids | watched_movie_ids | passed_movie_ids)
    if positive_movie_ids or onboarding_genre_ids or subscribed_ott_ids:
        if interaction_count == 0:
            profile.profile_type = "onboarding-only"
        elif interaction_count < 5:
            profile.profile_type = "sparse-profile"
        elif interaction_count < 20:
            profile.profile_type = "light-profile"
        else:
            profile.profile_type = "established-profile"
    return profile


def load_saved_movie_ids(db: Session, user_id: int) -> set[int]:
    stmt = (
        select(PlaylistMovie.movie_id)
        .join(Playlist, Playlist.id == PlaylistMovie.playlist_id)
        .where(Playlist.user_id == user_id)
        .distinct()
    )
    return set(db.scalars(stmt).all())


def add_movie_feature_scores(
    db: Session,
    profile: UserProfile,
    movie_ids: set[int],
    *,
    action: str,
) -> None:
    if not movie_ids:
        return
    weights = PROFILE_ACTION_FEATURE_WEIGHTS[action]
    feature_loaders = (
        ("genre_scores", "genre", load_movie_genre_ids),
        ("keyword_scores", "keyword", load_movie_keyword_ids),
        ("actor_scores", "actor", load_movie_actor_ids),
        ("director_scores", "director", load_movie_director_ids),
    )
    movie_id_list = list(movie_ids)
    for score_attribute, feature_type, loader in feature_loaders:
        add_scores(
            getattr(profile, score_attribute),
            loader(db, movie_id_list),
            weights[feature_type],
        )


def load_interaction_movie_sets(db: Session, user_id: int) -> tuple[set[int], set[int], set[int]]:
    rows = db.execute(
        select(
            UserInteraction.movie_id,
            UserInteraction.is_pinned,
            UserInteraction.is_watched,
            UserInteraction.is_passed,
        ).where(UserInteraction.user_id == user_id)
    )
    pinned: set[int] = set()
    watched: set[int] = set()
    passed: set[int] = set()
    for movie_id, is_pinned, is_watched, is_passed in rows:
        if is_pinned:
            pinned.add(movie_id)
        if is_watched:
            watched.add(movie_id)
        if is_passed:
            passed.add(movie_id)
    return pinned, watched, passed


def load_movie_semantic_scores(db: Session, *, movie_ids: set[int]) -> tuple[dict[str, float], dict[str, float]]:
    if not movie_ids:
        return {}, {}
    active_build = get_active_build(db)
    if active_build is None:
        return {}, {}

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
        {"build_id": active_build.id, "movie_ids": [str(movie_id) for movie_id in movie_ids]},
    )

    theme_scores: dict[str, float] = {}
    mood_scores: dict[str, float] = {}
    for node_type, ref_id, score in rows:
        if node_type == "theme":
            theme_scores[ref_id] = float(score or 0.0)
        elif node_type == "mood":
            mood_scores[ref_id] = float(score or 0.0)
    return theme_scores, mood_scores
