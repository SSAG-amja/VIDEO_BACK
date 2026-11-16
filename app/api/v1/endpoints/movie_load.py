import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.crud import movie as movie_crud
from app.crud import movie_detail as movie_detail_crud
from app.db.session import SessionLocal
from app.models.mapping import user_otts
from app.models.ott import Ott
from app.schemas.recsys import RecommendationMode
from app.services.recsys.contracts import RecommendationQuery
from app.services.recsys.executor import recommendation_executor
from app.services.recsys.registry import get_recommendation_adapter

router = APIRouter()
logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
SHORTS_PAGE_SIZE = 20
PREFETCH_PROVIDERS_COUNT = 10
YOUTUBE_CACHE_TTL_SECONDS = 6 * 60 * 60
YOUTUBE_CHECK_TIMEOUT_SECONDS = 0.8
MOVIE_CARD_CACHE_TTL_SECONDS = 60 * 60
_YOUTUBE_PLAYABLE_CACHE: dict[str, tuple[bool, float]] = {}
_MOVIE_CARD_CACHE: dict[tuple[int, bool], tuple[dict | None, float]] = {}
_YOUTUBE_RESTRICTED_MARKERS = (
    "login_required",
    "sign in to confirm your age",
    "confirm your age",
    "age-restricted",
    '"status":"unplayable"',
    '"status":"error"',
    "playback on other websites has been disabled",
    "video unavailable",
    "this video is private",
    "not available in your country",
    "blocked it in your country",
    "not made this video available",
    "국가에서는 동영상을 재생할 수 없습니다",
    "해당 국가에서 시청할 수 없",
    "사용자의 국가에서 볼 수 있도록 설정하지 않았습니다",
    "재생할 수 없는 동영상입니다",
)
PROVIDER_ID_ALIASES = {
    8: {8, 1796},
    1796: {8, 1796},
    96: {96, 1883},
    1883: {96, 1883},
}


def _extract_kr_streaming_providers(watch_providers_payload: dict | None) -> list[dict]:
    if not watch_providers_payload:
        return []

    flatrate = watch_providers_payload.get("results", {}).get("KR", {}).get("flatrate", [])
    providers = []
    seen_ids: set[int] = set()
    for provider in flatrate:
        provider_id = provider.get("provider_id")
        if not provider_id or provider_id in seen_ids:
            continue
        seen_ids.add(provider_id)
        provider_name = provider.get("provider_name")
        providers.append(
            {
                "ott_id": provider_id,
                "provider_id": provider_id,
                "ott_name": provider_name,
                "provider_name": provider_name,
                "logo_path": provider.get("logo_path"),
                "type": "streaming",
            }
        )

    has_standard_netflix = any(
        p["provider_id"] == 8 or (p.get("provider_name") or "").strip().lower() == "netflix"
        for p in providers
    )
    if has_standard_netflix:
        providers = [
            p
            for p in providers
            if p["provider_id"] != 1796
            and "standard with ads" not in (p.get("provider_name") or "").strip().lower()
        ]

    return providers


async def _is_youtube_trailer_playable(client: httpx.AsyncClient, youtube_id: str) -> bool:
    """YouTube embed 페이지를 한국(KR) 기준으로 확인해 연령제한/지역제한/퍼가기 불가 영상을 걸러낸다."""
    now = time.monotonic()
    cached = _YOUTUBE_PLAYABLE_CACHE.get(youtube_id)
    if cached is not None:
        playable, expires_at = cached
        if now < expires_at:
            return playable

    try:
        res = await client.get(
            f"https://www.youtube.com/embed/{youtube_id}",
            params={"gl": "KR", "hl": "en"},
            headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"},
            timeout=YOUTUBE_CHECK_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if res.status_code in (401, 403, 404, 410):
            _YOUTUBE_PLAYABLE_CACHE[youtube_id] = (False, now + YOUTUBE_CACHE_TTL_SECONDS)
            return False

        if res.status_code == 200:
            lower_html = res.text.lower()
            playable = not any(marker in lower_html for marker in _YOUTUBE_RESTRICTED_MARKERS)
            _YOUTUBE_PLAYABLE_CACHE[youtube_id] = (playable, now + YOUTUBE_CACHE_TTL_SECONDS)
            return playable
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.debug("YouTube trailer check skipped (%s): %s", youtube_id, exc)
        return True

    return True


def _select_primary_youtube_id(videos: list[dict]) -> str | None:
    for video in videos:
        if video.get("site") != "YouTube" or video.get("type") != "Trailer":
            continue
        if video.get("iso_639_1") != "ko":
            continue
        key = video.get("key")
        if isinstance(key, str) and key:
            return key

    return None


async def _pick_playable_youtube_id(
    client: httpx.AsyncClient,
    videos: list[dict],
) -> str | None:
    youtube_id = _select_primary_youtube_id(videos)
    if not youtube_id:
        return None

    if not await _is_youtube_trailer_playable(client, youtube_id):
        return None

    return youtube_id


# 2026.05.23 김호영
# 홈 숏츠 카드에 필요한 TMDB 상세, 트레일러, 출연진(상위 카드는 OTT 포함) 데이터를 조회한다.
async def _fetch_movie_full_data(client: httpx.AsyncClient, movie_id: int, include_providers: bool = False):
    now = time.monotonic()
    cache_key = (movie_id, include_providers)
    cached_card = _MOVIE_CARD_CACHE.get(cache_key)
    if cached_card is not None:
        payload, expires_at = cached_card
        if now < expires_at:
            return dict(payload) if payload is not None else None

    append_fields = "videos,credits,watch/providers" if include_providers else "videos,credits"
    detail_url = (
        f"{BASE_URL}/movie/{movie_id}"
        f"?api_key={settings.TMDB_API_KEY}"
        f"&language=ko-KR"
        f"&append_to_response={append_fields}"
        f"&include_video_language=ko"
    )
    res = await client.get(detail_url)

    if res.status_code != 200:
        return None

    data = res.json()
    videos = data.get("videos", {}).get("results", [])
    youtube_id = await _pick_playable_youtube_id(client, videos)

    cast_data = data.get("credits", {}).get("cast", [])[:10]
    formatted_cast = [
        {
            "id": person["id"],
            "name": person.get("name", ""),
            "role": person.get("character", ""),
            "image": f"https://image.tmdb.org/t/p/w200{person['profile_path']}" if person.get("profile_path") else "https://via.placeholder.com/200x300?text=No+Image",
        }
        for person in cast_data
    ]

    release_date = data.get("release_date", "")
    release_year = release_date[:4] if release_date else "미상"
    genres = ", ".join([g["name"] for g in data.get("genres", [])])
    runtime = data.get("runtime", 0)
    rating = round(data.get("vote_average", 0), 1)
    real_tags = [f"#{g['name']}" for g in data.get("genres", [])[:3]]

    payload = {
        "id": data["id"],
        "title": data["title"],
        "overview": data.get("overview", "시놉시스 정보가 없습니다."),
        "posterPath": data.get("poster_path", ""),
        "youtubeId": youtube_id,
        "info": f"{release_year} | {genres} | {runtime}분",
        "rating": rating,
        "runtime": runtime,
        "tags": real_tags if real_tags else ["#추천영화"],
        "cast": formatted_cast,
    }
    if include_providers:
        payload["providers"] = _extract_kr_streaming_providers(data.get("watch/providers"))
    _MOVIE_CARD_CACHE[cache_key] = (dict(payload), time.monotonic() + MOVIE_CARD_CACHE_TTL_SECONDS)
    return payload


# 2026.05.23 김호영
# TMDB movie id 목록을 홈 숏츠 카드 목록으로 변환하고 트레일러 없는 영화는 제외한다.
# subscribed_provider_ids가 전달되면 한국(KR) 실시간 스트리밍(flatrate) 기준으로 한 번 더 필터링한다.
async def _fetch_shorts_cards(
    tmdb_movie_ids: list[int],
    subscribed_provider_ids: set[int] | None = None,
) -> list[dict]:
    if subscribed_provider_ids is not None and not subscribed_provider_ids:
        return []

    need_all_providers = subscribed_provider_ids is not None
    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_movie_full_data(
                client,
                movie_id,
                include_providers=need_all_providers or index < PREFETCH_PROVIDERS_COUNT,
            )
            for index, movie_id in enumerate(tmdb_movie_ids)
        ]
        full_movies_data = await asyncio.gather(*tasks)

    cards = [movie for movie in full_movies_data if movie is not None and movie.get("youtubeId")]
    if subscribed_provider_ids is not None:
        cards = [
            movie
            for movie in cards
            if any(
                int(provider["provider_id"]) in subscribed_provider_ids
                for provider in movie.get("providers") or []
                if provider.get("provider_id") is not None
            )
        ]
    return cards


# 2026.05.23 김호영
# VIDEO_RECSYS 호출 실패 시 사용할 TMDB popular 기반 홈 숏츠 fallback을 만든다.
async def _fetch_popular_fallback(
    page: int,
    subscribed_provider_ids: set[int] | None = None,
) -> list[dict]:
    async with httpx.AsyncClient() as client:
        movies_url = f"{BASE_URL}/movie/popular?api_key={settings.TMDB_API_KEY}&language=ko-KR&page={page}"
        movies_res = await client.get(movies_url)

        if movies_res.status_code != 200:
            raise HTTPException(status_code=500, detail="TMDB API 호출 실패")

        movies = movies_res.json().get("results", [])
        return await _fetch_shorts_cards(
            [movie["id"] for movie in movies],
            subscribed_provider_ids=subscribed_provider_ids,
        )


def _load_user_subscribed_provider_ids(user_id: int) -> set[int]:
    with SessionLocal() as db:
        tmdb_ids = db.scalars(
            select(Ott.tmdb_id)
            .join(user_otts, user_otts.c.ott_id == Ott.id)
            .where(user_otts.c.user_id == user_id)
        ).all()

    expanded: set[int] = set()
    for tmdb_id in tmdb_ids:
        if tmdb_id is None:
            continue
        provider_id = int(tmdb_id)
        expanded.update(PROVIDER_ID_ALIASES.get(provider_id, {provider_id}))
    return expanded


def _load_shorts_tmdb_ids(
    *,
    user_id: int,
    page: int,
    shuffle_seed: str | None,
    mode: RecommendationMode,
) -> list[int]:
    offset = (page - 1) * SHORTS_PAGE_SIZE
    with SessionLocal() as db:
        recommendation = get_recommendation_adapter().get_recommendations(
            db,
            RecommendationQuery(
                user_id=user_id,
                mode=mode,
                limit=SHORTS_PAGE_SIZE,
                offset=offset,
                shuffle_seed=shuffle_seed,
            ),
        )
        recommended_movies = movie_crud.get_movies_by_internal_ids_preserve_order(
            db,
            recommendation.movie_ids,
        )
        return [int(movie["tmdb_id"]) for movie in recommended_movies]


# 2026.06.04 김호영
# VIDEO_RECSYS HTTP 호출 대신 BACK 내부 추천 service를 사용해 홈 숏츠 후보를 조회한다.
# 2026.05.23 김호영
# VIDEO_RECSYS 추천 후보를 홈 숏츠 카드 형태로 반환한다.
@router.get("/shorts")
async def get_shorts_movies(
    current_user=Depends(deps.get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    shuffle_seed: str | None = Query(None, description="Stable shuffle seed for a refresh session"),
    mode: RecommendationMode = Query(
        RecommendationMode.SUBSCRIBED_ONLY,
        description="Recommendation mode (all or subscribed_only)",
    ),
):
    subscribed_provider_ids: set[int] | None = None
    if mode == RecommendationMode.SUBSCRIBED_ONLY:
        subscribed_provider_ids = await recommendation_executor.run(
            _load_user_subscribed_provider_ids,
            int(current_user.id),
        )

    try:
        tmdb_movie_ids = await recommendation_executor.run(
            _load_shorts_tmdb_ids,
            user_id=int(current_user.id),
            page=page,
            shuffle_seed=shuffle_seed,
            mode=mode,
        )
    except Exception:
        logger.warning(
            "shorts recommendation failed user_id=%s mode=%s",
            current_user.id,
            mode,
            exc_info=True,
        )
        return {
            "movies": await _fetch_popular_fallback(
                page,
                subscribed_provider_ids=subscribed_provider_ids,
            ),
            "source": "fallback",
        }

    return {
        "movies": await _fetch_shorts_cards(
            tmdb_movie_ids,
            subscribed_provider_ids=subscribed_provider_ids,
        ),
        "source": "recsys",
    }


# 2026.05.13 박현식
# DB 우선 영화 상세 조회 결과를 반환하고 부족한 상세 필드는 TMDB로 보강한다.
@router.get("/{movie_id}")
async def get_movie_detail(movie_id: int, db: Session = Depends(deps.get_db)):
    return await movie_detail_crud.build_movie_detail(db, movie_id)
