from fastapi import APIRouter, HTTPException, Query
from app.core.config import settings
import httpx
import asyncio

router = APIRouter()

BASE_URL = "https://api.themoviedb.org/3"

@router.get("/shorts")
async def get_shorts_movies(page: int = Query(1, description="페이지 번호")):
    """
    프론트엔드의 숏폼 UI와 '상세 정보 모달'에 필요한 모든 데이터를 한 번에 반환합니다.
    """
    async with httpx.AsyncClient() as client:
        # 1. 인기 영화 목록 가져오기 (settings.TMDB_API_KEY 사용)
        movies_url = f"{BASE_URL}/movie/popular?api_key={settings.TMDB_API_KEY}&language=ko-KR&page={page}"
        movies_res = await client.get(movies_url)
        
        if movies_res.status_code != 200:
            raise HTTPException(status_code=500, detail="TMDB API 호출 실패")
            
        movies = movies_res.json().get("results", [])

        # 2. 개별 영화의 상세 정보(예고편, 출연진 등)를 가져오는 비동기 함수
        async def fetch_movie_full_data(movie_id: int):
            # append_to_response를 사용하여 상세정보, 비디오, 출연진을 한 번에 호출
            detail_url = f"{BASE_URL}/movie/{movie_id}?api_key={settings.TMDB_API_KEY}&language=ko-KR&append_to_response=videos,credits"
            res = await client.get(detail_url)
            
            if res.status_code != 200:
                return None
                
            data = res.json()

            # --- 트레일러(YouTube ID) 추출 ---
            videos = data.get("videos", {}).get("results", [])
            youtube_id = next((v["key"] for v in videos if v["site"] == "YouTube" and v["type"] == "Trailer"), None)
            
            # 한국어 예고편이 없다면 기본(영어) 예고편으로 재시도
            if not youtube_id:
                en_url = f"{BASE_URL}/movie/{movie_id}/videos?api_key={settings.TMDB_API_KEY}"
                en_res = await client.get(en_url)
                en_videos = en_res.json().get("results", [])
                youtube_id = next((v["key"] for v in en_videos if v["site"] == "YouTube" and v["type"] == "Trailer"), None)

            # --- 출연진(Cast) 정보 추출 (최대 10명) ---
            cast_data = data.get("credits", {}).get("cast", [])[:10]
            formatted_cast = [
                {
                    "id": person["id"],
                    "name": person["name"],
                    "role": person["character"],
                    "image": f"https://image.tmdb.org/t/p/w200{person['profile_path']}" if person.get("profile_path") else "https://via.placeholder.com/200x300?text=No+Image"
                }
                for person in cast_data
            ]

            # --- 데이터 포맷팅 ---
            release_date = data.get("release_date", "")
            release_year = release_date[:4] if release_date else "미상"
            genres = ", ".join([g["name"] for g in data.get("genres", [])])
            
            runtime = data.get("runtime", 0)
            vote_average = data.get("vote_average", 0)
            rating = round(vote_average, 1)
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
                "cast": formatted_cast
            }

        # 3. 병렬 비동기 호출로 속도 최적화
        tasks = [fetch_movie_full_data(movie["id"]) for movie in movies]
        full_movies_data = await asyncio.gather(*tasks)

        # 4. 유효한 데이터(유튜브 ID 포함)만 필터링
        final_movies = [m for m in full_movies_data if m is not None and m.get("youtubeId")]

        return {"movies": final_movies}