import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.crud import movie_detail as movie_detail_crud

router = APIRouter()

BASE_URL = "https://api.themoviedb.org/3"


# 2026.05.13 박현식
# 임시 홈 숏츠 피드용 TMDB popular 영화를 트레일러 카드 형태로 반환한다.
@router.get("/shorts")
async def get_shorts_movies(page: int = Query(1, description="Page number")):
    async with httpx.AsyncClient() as client:
        movies_url = f"{BASE_URL}/movie/popular?api_key={settings.TMDB_API_KEY}&language=ko-KR&page={page}"
        movies_res = await client.get(movies_url)

        if movies_res.status_code != 200:
            raise HTTPException(status_code=500, detail="TMDB API 호출 실패")

        movies = movies_res.json().get("results", [])

        # 2026.05.13 박현식
        # TMDB 영화 상세에서 트레일러와 출연진을 추출해 홈 카드 데이터로 변환한다.
        async def fetch_movie_full_data(movie_id: int):
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

        tasks = [fetch_movie_full_data(movie["id"]) for movie in movies]
        full_movies_data = await asyncio.gather(*tasks)
        final_movies = [m for m in full_movies_data if m is not None and m.get("youtubeId")]

        return {"movies": final_movies}


# 2026.05.13 박현식
# DB 우선 영화 상세 조회 결과를 반환하고 부족한 상세 필드는 TMDB로 보강한다.
@router.get("/{movie_id}")
async def get_movie_detail(movie_id: int, db: Session = Depends(deps.get_db)):
    return await movie_detail_crud.build_movie_detail(db, movie_id)
