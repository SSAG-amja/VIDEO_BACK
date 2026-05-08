from fastapi import APIRouter, HTTPException, Query, Depends
from app.core.config import settings
from app.schemas.movie import recommendedMovie
from app.api import deps
from sqlalchemy.orm import Session

from app.crud import movie as movie_crud
from app.crud import user as user_crud

import httpx

router = APIRouter()

# 20260508 김광원
# 사용자 선호 장르 기반 영화 추천 API
@router.get("/movies/recommended", response_model=recommendedMovie)
def get_recommended_movies(
    db: Session = Depends(deps.get_db),
    current_user = Depends(deps.get_current_user),
    page: int = Query(1, description="페이지 번호 (기본값: 1)")
):
    limit = 200
    skip = (page - 1) * limit
    try:
        recommended_movies = movie_crud.get_recommended_movies(db, current_user.id, skip=skip, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"data": recommended_movies}







"""
밑에는 임의로 작성됐던 코드
"""

# 20260330 박현식 
#  탐색(Explore) 및 검색 관련 API

BASE_URL = "https://api.themoviedb.org/3"

@router.get("/search")
async def search_tmdb(q: str = Query(..., description="검색할 영화 제목, 배우, 감독 이름")):
    """
    TMDB Multi Search API를 호출하여 영화, 인물 검색 결과를 프론트엔드 UI에 맞게 가공하여 반환합니다.
    """
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/search/multi"
        params = {
            "api_key": settings.TMDB_API_KEY,
            "query": q,
            "language": "ko-KR",
            "page": 1,
            "include_adult": "false"
        }
        
        res = await client.get(url, params=params)
        
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="TMDB API 호출 실패")
            
        data = res.json()
        results = data.get("results", [])
        
        formatted_movies = []
        
        for item in results:
            # 1. 일반 영화 검색 결과인 경우
            if item.get("media_type") == "movie" and item.get("poster_path"):
                formatted_movies.append({
                    "id": str(item["id"]),
                    "title": item.get("title") or item.get("original_title", ""),
                    "rating": round(item.get("vote_average", 0), 1),
                    "image": f"https://image.tmdb.org/t/p/w500{item['poster_path']}",
                    "badge": None # 배지 없음
                })
                
            # 2. 인물(배우/감독) 검색 결과인 경우 -> 대표작(known_for) 추출
            elif item.get("media_type") == "person":
                person_name = item.get("name", "")
                dept = item.get("known_for_department", "")
                
                # 감독인지 카메오/배우인지 구분
                role_text = "감독작" if dept == "Directing" else "출연작"
                badge_text = f"{person_name} {role_text}"
                
                for known in item.get("known_for", []):
                    # 대표작 중 '영화'이고 포스터가 있는 것만 추출
                    if known.get("media_type") == "movie" and known.get("poster_path"):
                        formatted_movies.append({
                            "id": str(known["id"]),
                            "title": known.get("title") or known.get("original_title", ""),
                            "rating": round(known.get("vote_average", 0), 1),
                            "image": f"https://image.tmdb.org/t/p/w500{known['poster_path']}",
                            "badge": badge_text # 예: "송강호 출연작", "봉준호 감독작"
                        })
                        
        # 중복된 영화 제거 (같은 영화가 여러 번 들어가는 것 방지)
        seen_ids = set()
        final_movies = []
        for m in formatted_movies:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                final_movies.append(m)

        return {"movies": final_movies}
    

#260330 박현식
#탐색탭 메인화면 로직 및 해쉬태그별 검색

@router.get("/recommend")
async def get_recommendations(tag: str = Query("#대한민국 인기작", description="선택된 무드/추천 태그")):
    """
    프론트엔드에서 선택한 태그에 따라 맞춤형 결과를 반환합니다.
    """
    async with httpx.AsyncClient() as client:
        base_params = {
            "api_key": settings.TMDB_API_KEY,
            "language": "ko-KR",
            "page": 1
        }
        
        # 1. 태그별 TMDB API 분기 처리
        if tag == "#전세계 인기작":
            url = f"{BASE_URL}/movie/popular"
        elif tag == "#대한민국 인기작":
            url = f"{BASE_URL}/movie/popular"
            base_params["region"] = "KR" 
        elif tag == "#평점 높은 명작":
            url = f"{BASE_URL}/movie/top_rated"
        elif tag == "#도파민 폭발 액션":
            url = f"{BASE_URL}/discover/movie"
            base_params["with_genres"] = "28,53" # 액션, 스릴러
            base_params["sort_by"] = "popularity.desc"
        elif tag == "#가볍게 웃기 좋은":
            url = f"{BASE_URL}/discover/movie"
            base_params["with_genres"] = "35" # 코미디
            base_params["sort_by"] = "popularity.desc"
        else:
            url = f"{BASE_URL}/movie/popular" # 기본값

        res = await client.get(url, params=base_params)
        
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="TMDB 추천 API 호출 실패")
            
        data = res.json()
        results = data.get("results", [])
        
        # 2. 프론트엔드 형식에 맞게 데이터 가공
        formatted_movies = []
        for item in results:
            if item.get("poster_path"):
                formatted_movies.append({
                    "id": str(item["id"]),
                    "title": item.get("title") or item.get("original_title", ""),
                    "rating": round(item.get("vote_average", 0), 1),
                    "image": f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
                })

        return {"movies": formatted_movies}