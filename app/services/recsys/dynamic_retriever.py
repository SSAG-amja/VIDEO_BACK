from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.recsys.movies import (
    find_contextual_movies,
    find_genre_popular_movies,
    find_popular_movies,
    iter_matching_movies,
    load_streaming_movie_ids,
)
from app.crud.recsys.onboarding import (
    load_movie_actor_ids,
    load_movie_director_ids,
    load_movie_genre_ids,
    load_movie_keyword_ids,
    load_user_favorite_movie_ids,
    load_user_genre_ids,
    load_user_ott_ids,
)
from app.crud.recsys.recommendations import replace_user_recommendation_rows
from app.crud.recsys.users import user_exists
from app.models.mapping import MovieActor, movie_directors, movie_genres, movie_keywords
from app.models.movie import Movie
from app.models.recommendations import Recommendation
from app.schemas.recsys import ColdStartRequest


SOURCE_FAVORITE_SIMILAR = "favorite_similar"
SOURCE_GENRE_POPULAR = "genre_popular"
SOURCE_POPULAR_FALLBACK = "popular_fallback"

SIMILAR_RATIO = 0.5
GENRE_RATIO = 0.3
FALLBACK_RATIO = 0.2

GENRE_MATCH_WEIGHT = 8.0
KEYWORD_MATCH_WEIGHT = 5.0
DIRECTOR_MATCH_WEIGHT = 6.0
ACTOR_MATCH_WEIGHT = 3.0
POPULARITY_WEIGHT = 0.05
VOTE_AVERAGE_WEIGHT = 1.0


class UserNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateScore:
    movie_id: int
    score: float
    source: str


@dataclass(frozen=True)
class OnboardingProfile:
    genre_ids: list[int]
    ott_ids: list[int]
    favorite_movie_ids: list[int]
    favorite_genre_ids: list[int]
    favorite_keyword_ids: list[int]
    favorite_actor_ids: list[int]
    favorite_director_ids: list[int]


# 2026.06.04 김호영
# 동적 후보 조회에 제외 movie id 목록을 전달해 중복 추천을 줄인다.
def find_contextual_candidates(
    db: Session,
    *,
    genre_ids: list[int] | None = None,
    ott_ids: list[int] | None = None,
    limit: int = 100,
    excluded_movie_ids: set[int] | None = None,
) -> list[Movie]:
    return find_contextual_movies(
        db,
        genre_ids=genre_ids,
        ott_ids=ott_ids,
        limit=limit,
        excluded_movie_ids=excluded_movie_ids,
    )


# 2026.06.04 김호영
# 온보딩 프로필 기반 cold-start 추천 후보를 생성해 recommendations 테이블에 저장한다.
def build_cold_start_pool(db: Session, payload: ColdStartRequest, pool_size: int | None = None) -> int:
    target_pool_size = pool_size or settings.RECOMMENDATION_POOL_SIZE
    ensure_user_exists(db, payload.user_id)
    profile = load_onboarding_profile(db, payload.user_id)
    allocation = allocate_candidate_counts(target_pool_size)

    candidates: list[CandidateScore] = []
    candidates.extend(
        find_favorite_similar_candidates(
            db,
            profile,
            limit=allocation[SOURCE_FAVORITE_SIMILAR] * 3,
        )
    )
    candidates.extend(
        find_genre_popular_candidates(
            db,
            genre_ids=profile.genre_ids,
            excluded_movie_ids=profile.favorite_movie_ids,
            limit=allocation[SOURCE_GENRE_POPULAR] * 3,
        )
    )

    ranked = merge_candidate_scores(
        candidates,
        source_limits={
            SOURCE_FAVORITE_SIMILAR: allocation[SOURCE_FAVORITE_SIMILAR],
            SOURCE_GENRE_POPULAR: allocation[SOURCE_GENRE_POPULAR],
        },
    )

    if len(ranked) < allocation[SOURCE_FAVORITE_SIMILAR] + allocation[SOURCE_GENRE_POPULAR]:
        ranked = merge_candidate_scores(candidates, source_limits=None)

    if len(ranked) < target_pool_size:
        fallback = find_popular_fallback_candidates(
            db,
            excluded_movie_ids=set(profile.favorite_movie_ids) | {candidate.movie_id for candidate in ranked},
            limit=target_pool_size - len(ranked),
        )
        ranked = merge_candidate_scores([*ranked, *fallback], source_limits=None)

    ranked = apply_ott_bonus(
        ranked,
        subscribed_movie_ids=load_streaming_movie_ids(db, profile.ott_ids, [candidate.movie_id for candidate in ranked]),
        bonus_multiplier=settings.SUBSCRIBED_OTT_BONUS,
    )

    replace_user_recommendations(db, payload.user_id, ranked[:target_pool_size])
    return min(len(ranked), target_pool_size)


# 2026.06.04 김호영
# 추천 대상 사용자가 존재하지 않으면 명시적인 예외를 발생시킨다.
def ensure_user_exists(db: Session, user_id: int) -> None:
    if not user_exists(db, user_id):
        raise UserNotFoundError(f"user_id={user_id} not found")


# 2026.06.04 김호영
# 온보딩 입력과 선호 영화 feature를 한 번에 로드한다.
def load_onboarding_profile(db: Session, user_id: int) -> OnboardingProfile:
    favorite_movie_ids = load_user_favorite_movie_ids(db, user_id)
    genre_ids = load_user_genre_ids(db, user_id)
    ott_ids = load_user_ott_ids(db, user_id)
    favorite_genre_ids = load_movie_genre_ids(db, favorite_movie_ids)
    favorite_keyword_ids = load_movie_keyword_ids(db, favorite_movie_ids)
    favorite_actor_ids = load_movie_actor_ids(db, favorite_movie_ids)
    favorite_director_ids = load_movie_director_ids(db, favorite_movie_ids)

    return OnboardingProfile(
        genre_ids=genre_ids,
        ott_ids=ott_ids,
        favorite_movie_ids=favorite_movie_ids,
        favorite_genre_ids=favorite_genre_ids,
        favorite_keyword_ids=favorite_keyword_ids,
        favorite_actor_ids=favorite_actor_ids,
        favorite_director_ids=favorite_director_ids,
    )


def find_favorite_similar_candidates(
    db: Session,
    profile: OnboardingProfile,
    *,
    limit: int,
) -> list[CandidateScore]:
    scores: dict[int, CandidateScore] = {}
    excluded = set(profile.favorite_movie_ids)

    _accumulate_mapping_candidates(
        db,
        scores,
        mapping_source=movie_genres,
        movie_column=movie_genres.c.movie_id,
        match_column=movie_genres.c.genre_id,
        match_ids=profile.favorite_genre_ids,
        weight=GENRE_MATCH_WEIGHT,
        source=SOURCE_FAVORITE_SIMILAR,
        excluded_movie_ids=excluded,
        limit=limit,
    )
    _accumulate_mapping_candidates(
        db,
        scores,
        mapping_source=movie_keywords,
        movie_column=movie_keywords.c.movie_id,
        match_column=movie_keywords.c.keyword_id,
        match_ids=profile.favorite_keyword_ids,
        weight=KEYWORD_MATCH_WEIGHT,
        source=SOURCE_FAVORITE_SIMILAR,
        excluded_movie_ids=excluded,
        limit=limit,
    )
    _accumulate_mapping_candidates(
        db,
        scores,
        mapping_source=movie_directors,
        movie_column=movie_directors.c.movie_id,
        match_column=movie_directors.c.director_id,
        match_ids=profile.favorite_director_ids,
        weight=DIRECTOR_MATCH_WEIGHT,
        source=SOURCE_FAVORITE_SIMILAR,
        excluded_movie_ids=excluded,
        limit=limit,
    )
    _accumulate_mapping_candidates(
        db,
        scores,
        mapping_source=MovieActor,
        movie_column=MovieActor.movie_id,
        match_column=MovieActor.actor_id,
        match_ids=profile.favorite_actor_ids,
        weight=ACTOR_MATCH_WEIGHT,
        source=SOURCE_FAVORITE_SIMILAR,
        excluded_movie_ids=excluded,
        limit=limit,
    )
    return sorted(scores.values(), key=lambda candidate: candidate.score, reverse=True)[:limit]


def find_genre_popular_candidates(
    db: Session,
    *,
    genre_ids: list[int],
    excluded_movie_ids: list[int],
    limit: int,
) -> list[CandidateScore]:
    if not genre_ids or limit <= 0:
        return []

    return [
        CandidateScore(
            movie_id=movie.id,
            score=movie_score(movie),
            source=SOURCE_GENRE_POPULAR,
        )
        for movie in find_genre_popular_movies(
            db,
            genre_ids=genre_ids,
            excluded_movie_ids=excluded_movie_ids,
            limit=limit,
        )
    ]


def find_popular_fallback_candidates(
    db: Session,
    *,
    excluded_movie_ids: set[int],
    limit: int,
) -> list[CandidateScore]:
    if limit <= 0:
        return []

    return [
        CandidateScore(
            movie_id=movie.id,
            score=movie_score(movie),
            source=SOURCE_POPULAR_FALLBACK,
        )
        for movie in find_popular_movies(db, excluded_movie_ids=excluded_movie_ids, limit=limit)
    ]


def replace_user_recommendations(db: Session, user_id: int, candidates: list[CandidateScore]) -> None:
    try:
        rows = [
            Recommendation(
                user_id=user_id,
                movie_id=candidate.movie_id,
                score=candidate.score,
                rank=rank,
                source=candidate.source,
                source_scores={candidate.source: candidate.score},
            )
            for rank, candidate in enumerate(candidates, start=1)
        ]
        replace_user_recommendation_rows(db, user_id, rows)
        db.commit()
    except Exception:
        db.rollback()
        raise


def allocate_candidate_counts(pool_size: int) -> dict[str, int]:
    similar_count = int(pool_size * SIMILAR_RATIO)
    genre_count = int(pool_size * GENRE_RATIO)
    fallback_count = max(pool_size - similar_count - genre_count, 0)
    return {
        SOURCE_FAVORITE_SIMILAR: similar_count,
        SOURCE_GENRE_POPULAR: genre_count,
        SOURCE_POPULAR_FALLBACK: fallback_count,
    }


def movie_score(movie: Movie) -> float:
    return ((movie.popularity or 0.0) * POPULARITY_WEIGHT) + ((movie.vote_average or 0.0) * VOTE_AVERAGE_WEIGHT)


def merge_candidate_scores(
    candidates: list[CandidateScore],
    *,
    source_limits: dict[str, int] | None,
) -> list[CandidateScore]:
    selected: dict[int, CandidateScore] = {}
    source_counts: dict[str, int] = {}

    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if source_limits is not None:
            current_count = source_counts.get(candidate.source, 0)
            if current_count >= source_limits.get(candidate.source, 0):
                continue

        existing = selected.get(candidate.movie_id)
        if existing is None:
            selected[candidate.movie_id] = candidate
            source_counts[candidate.source] = source_counts.get(candidate.source, 0) + 1
            continue

        selected[candidate.movie_id] = CandidateScore(
            movie_id=candidate.movie_id,
            score=existing.score + candidate.score,
            source=existing.source,
        )

    return sorted(selected.values(), key=lambda item: item.score, reverse=True)


def apply_ott_bonus(
    candidates: list[CandidateScore],
    *,
    subscribed_movie_ids: set[int],
    bonus_multiplier: float,
) -> list[CandidateScore]:
    boosted = [
        CandidateScore(
            movie_id=candidate.movie_id,
            score=candidate.score * bonus_multiplier if candidate.movie_id in subscribed_movie_ids else candidate.score,
            source=candidate.source,
        )
        for candidate in candidates
    ]
    return sorted(boosted, key=lambda item: item.score, reverse=True)


def _accumulate_mapping_candidates(
    db: Session,
    scores: dict[int, CandidateScore],
    *,
    mapping_source,
    movie_column,
    match_column,
    match_ids: list[int],
    weight: float,
    source: str,
    excluded_movie_ids: set[int],
    limit: int,
) -> None:
    for movie, match_count in iter_matching_movies(
        db,
        mapping_source,
        movie_column,
        match_column,
        match_ids,
        excluded_movie_ids,
        limit,
    ):
        score = movie_score(movie) + (float(match_count) * weight)
        existing = scores.get(movie.id)
        if existing is None:
            scores[movie.id] = CandidateScore(movie_id=movie.id, score=score, source=source)
            continue
        scores[movie.id] = CandidateScore(movie_id=movie.id, score=existing.score + score, source=source)
