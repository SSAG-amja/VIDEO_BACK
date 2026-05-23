import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.crud import movie as movie_crud
from app.crud import movie_detail as movie_detail_crud
from app.services import recsys_client

router = APIRouter()

BASE_URL = "https://api.themoviedb.org/3"
SHORTS_PAGE_SIZE = 20


# 2026.05.23 김호영
# 홈 숏츠 카드에 필요한 TMDB 상세, 트레일러, 출연진 데이터를 조회한다.
async def _fetch_movie_full_data(client: httpx.AsyncClient, movie_id: int):
    detail_url = f"{BASE_URL}/movie/{movie_id}?api_key={settings.TMDB_API_KEY}&language=ko-KR&append_to_response=videos,credits"
    res = await client.get(detail_url)

    if res.status_code != 200:
        return None

    data = res.json()
    videos = data.get("videos", {}).get("results", [])
    youtube_id = next((v["key"] for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

    if not youtube_id:
        en_url = f"{BASE_URL}/movie/{movie_id}/videos?api_key={settings.TMDB_API_KEY}"
        en_res = await client.get(en_url)
        en_videos = en_res.json().get("results", []) if en_res.status_code == 200 else []
        youtube_id = next((v["key"] for v in en_videos if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)

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

    return {
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


# 2026.05.23 김호영
# TMDB movie id 목록을 홈 숏츠 카드 목록으로 변환하고 트레일러 없는 영화는 제외한다.
async def _fetch_shorts_cards(tmdb_movie_ids: list[int]) -> list[dict]:
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_movie_full_data(client, movie_id) for movie_id in tmdb_movie_ids]
        full_movies_data = await asyncio.gather(*tasks)
    return [movie for movie in full_movies_data if movie is not None and movie.get("youtubeId")]


# 2026.05.23 김호영
# VIDEO_RECSYS 호출 실패 시 사용할 TMDB popular 기반 홈 숏츠 fallback을 만든다.
async def _fetch_popular_fallback(page: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        movies_url = f"{BASE_URL}/movie/popular?api_key={settings.TMDB_API_KEY}&language=ko-KR&page={page}"
        movies_res = await client.get(movies_url)

        if movies_res.status_code != 200:
            raise HTTPException(status_code=500, detail="TMDB API 호출 실패")

        movies = movies_res.json().get("results", [])
        return await _fetch_shorts_cards([movie["id"] for movie in movies])


# 2026.05.23 김호영
# VIDEO_RECSYS 추천 후보를 홈 숏츠 카드 형태로 반환한다.
# 2026.05.23 VIDEO_RECSYS 추천 후보를 홈 숏츠 카드 형태로 반환한다.
@router.get("/shorts")
async def get_shorts_movies(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
):
    offset = (page - 1) * SHORTS_PAGE_SIZE
    recsys_movie_ids = recsys_client.get_recommendation_movie_ids(
        current_user.id,
        limit=SHORTS_PAGE_SIZE,
        offset=offset,
    )

    if recsys_movie_ids is None:
        return {"movies": await _fetch_popular_fallback(page), "source": "fallback"}

    recommended_movies = movie_crud.get_movies_by_internal_ids_preserve_order(db, recsys_movie_ids)
    tmdb_movie_ids = [movie["tmdb_id"] for movie in recommended_movies]
    return {"movies": await _fetch_shorts_cards(tmdb_movie_ids), "source": "recsys"}


# 2026.05.13 박현식
# DB 우선 영화 상세 조회 결과를 반환하고 부족한 상세 필드는 TMDB로 보강한다.
@router.get("/{movie_id}")
async def get_movie_detail(movie_id: int, db: Session = Depends(deps.get_db)):
    return await movie_detail_crud.build_movie_detail(db, movie_id)
