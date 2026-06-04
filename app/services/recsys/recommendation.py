from dataclasses import dataclass
from datetime import date
import hashlib
import random

from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.crud.recsys.interactions import load_excluded_interaction_movie_ids
from app.crud.recsys.movies import load_movie_ott_map
from app.crud.recsys.onboarding import load_user_ott_ids
from app.crud.recsys.recommendations import load_precomputed_candidates
from app.models.movie import Movie
from app.models.recommendations import Recommendation
from app.schemas.recsys import RecommendationMode, RecommendationResponse
from app.services.recsys.dynamic_retriever import ensure_user_exists, find_contextual_candidates
from app.services.recsys.interaction_cache import get_blacklisted_movie_ids


@dataclass(frozen=True)
class RecommendationOptions:
    user_id: int
    mode: RecommendationMode
    limit: int
    offset: int = 0
    shuffle_seed: str | None = None


# 2026.06.04 김호영
# 사전 계산 추천과 동적 추천을 offset 기반으로 이어 붙여 페이지 응답을 만든다.
def get_recommendations(
    db: Session,
    redis: Redis,
    settings: Settings,
    options: RecommendationOptions,
) -> RecommendationResponse:
    ensure_user_exists(db, options.user_id)
    blacklisted_ids = get_blacklisted_movie_ids(redis, options.user_id) | load_excluded_interaction_movie_ids(
        db, options.user_id
    )
    subscribed_ott_ids = set(load_user_ott_ids(db, options.user_id))
    rows = load_precomputed_candidates(db, options.user_id, settings.RECOMMENDATION_POOL_SIZE)

    ranked = _rank_rows(
        db,
        rows,
        blacklisted_ids=blacklisted_ids,
        subscribed_ott_ids=subscribed_ott_ids,
        settings=settings,
        options=options,
    )

    precomputed_movie_ids = {movie.id for _score, _recommendation, movie in ranked}
    target_count = options.offset + options.limit + 1
    if len(ranked) < target_count:
        excluded_movie_ids = blacklisted_ids | {movie.id for _recommendation, movie in rows}
        dynamic_ranked = _rank_dynamic_rows(
            db,
            settings=settings,
            options=options,
            blacklisted_ids=blacklisted_ids,
            subscribed_ott_ids=subscribed_ott_ids,
            excluded_movie_ids=excluded_movie_ids,
            limit=max(settings.RECOMMENDATION_POOL_SIZE, target_count - len(ranked)),
        )
        ranked = [*ranked, *dynamic_ranked]

    ranked = _shuffle_ranked_rows(ranked, options.shuffle_seed)
    page = ranked[options.offset : options.offset + options.limit]
    movie_ids = [movie.id for _score, _recommendation, movie in page]
    return RecommendationResponse(
        user_id=options.user_id,
        mode=options.mode,
        movie_ids=movie_ids,
        limit=options.limit,
        offset=options.offset,
        count=len(movie_ids),
        has_more=len(ranked) > options.offset + options.limit,
        source=_page_source(movie_ids, precomputed_movie_ids),
    )


# 2026.06.04 김호영
# 부족한 페이지 구간을 채울 동적 추천 후보를 랭킹 형식으로 변환한다.
def _rank_dynamic_rows(
    db: Session,
    *,
    settings: Settings,
    options: RecommendationOptions,
    blacklisted_ids: set[int],
    subscribed_ott_ids: set[int],
    excluded_movie_ids: set[int],
    limit: int,
) -> list[tuple[float, Recommendation, Movie]]:
    dynamic_rows = _as_dynamic_rows(
        find_contextual_candidates(
            db,
            ott_ids=subscribed_ott_ids if options.mode == RecommendationMode.SUBSCRIBED_ONLY else None,
            limit=limit,
            excluded_movie_ids=excluded_movie_ids,
        )
    )
    return _rank_rows(
        db,
        dynamic_rows,
        blacklisted_ids=blacklisted_ids,
        subscribed_ott_ids=subscribed_ott_ids,
        settings=settings,
        options=options,
    )


def _rank_rows(
    db: Session,
    rows: list[tuple[Recommendation, Movie]],
    *,
    blacklisted_ids: set[int],
    subscribed_ott_ids: set[int],
    settings: Settings,
    options: RecommendationOptions,
) -> list[tuple[float, Recommendation, Movie]]:
    ranked: list[tuple[float, Recommendation, Movie]] = []
    seen_movie_ids: set[int] = set()
    movie_ids = [movie.id for _recommendation, movie in rows]
    movie_ott_map = load_movie_ott_map(db, movie_ids)
    for recommendation, movie in rows:
        if movie.id in blacklisted_ids or movie.id in seen_movie_ids:
            continue
        seen_movie_ids.add(movie.id)

        movie_ott_ids = movie_ott_map.get(movie.id, set())
        is_subscribed = bool(subscribed_ott_ids and movie_ott_ids.intersection(subscribed_ott_ids))
        if options.mode == RecommendationMode.SUBSCRIBED_ONLY and not is_subscribed:
            continue
        if options.mode == RecommendationMode.SUBSCRIBED_ONLY and _is_unreleased(movie):
            continue

        score = recommendation.score
        if is_subscribed:
            score *= settings.SUBSCRIBED_OTT_BONUS

        ranked.append((score, recommendation, movie))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _as_dynamic_rows(movies: list[Movie]) -> list[tuple[Recommendation, Movie]]:
    return [
        (
            Recommendation(
                user_id=0,
                movie_id=movie.id,
                score=movie.popularity or 0.0,
                rank=index,
                source="dynamic",
                source_scores={"dynamic": movie.popularity or 0.0},
            ),
            movie,
        )
        for index, movie in enumerate(movies, start=1)
    ]


def _is_unreleased(movie: Movie) -> bool:
    return movie.release_date is not None and movie.release_date > date.today()


# 2026.06.04 김호영
# refresh 세션별 seed로 추천 후보 순서를 안정적으로 섞어 pagination 중복을 방지한다.
def _shuffle_ranked_rows(
    ranked: list[tuple[float, Recommendation, Movie]],
    shuffle_seed: str | None,
) -> list[tuple[float, Recommendation, Movie]]:
    if not shuffle_seed:
        return ranked

    shuffled = [*ranked]
    seed_value = int(hashlib.sha256(shuffle_seed.encode("utf-8")).hexdigest(), 16)
    random.Random(seed_value).shuffle(shuffled)
    return shuffled


# 2026.06.04 김호영
# 응답 페이지가 사전 계산 추천인지 동적 추천인지 source 값을 판정한다.
def _page_source(movie_ids: list[int], precomputed_movie_ids: set[int]) -> str:
    if not movie_ids:
        return "empty"
    precomputed_count = sum(1 for movie_id in movie_ids if movie_id in precomputed_movie_ids)
    if precomputed_count == len(movie_ids):
        return "precomputed"
    if precomputed_count == 0:
        return "dynamic"
    return "mixed"
