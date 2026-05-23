from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import movie as movie_crud
from app.services import recsys_client
from app.crud import movie_detail as movie_detail_crud
from app.schemas.movie import MovieSearchResponse, recommendedMovie

router = APIRouter()


# 2026.05.23 김호영
# 탐색 추천 목록을 VIDEO_RECSYS 결과 우선으로 조회하고 실패 시 기존 DB 추천으로 대체한다.
# 2026.05.13 박현식
# 사용자 선호 기반 추천 영화 목록을 반환한다.
@router.get("/movies/recommended", response_model=recommendedMovie)
def get_recommended_movies(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
):
    limit = 200
    skip = (page - 1) * limit
    try:
        recommended_movies = []
        recsys_movie_ids = recsys_client.get_recommendation_movie_ids(current_user.id, limit=limit, offset=skip)
        if recsys_movie_ids is not None:
            recommended_movies = movie_crud.get_movies_by_internal_ids_preserve_order(db, recsys_movie_ids)

        if recsys_movie_ids is None:
            recommended_movies = movie_crud.get_recommended_movies(db, current_user.id, skip=skip, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"data": recommended_movies}


# 2026.05.13 박현식
# 제목, 태그, 장르 조건으로 DB 영화 검색 결과를 반환한다.
@router.get("/movies/search", response_model=MovieSearchResponse)
def search_movies(
    db: Session = Depends(deps.get_db),
    title: str | None = Query(None, description="Legacy alias for query"),
    query: str | None = Query(None, description="Movie title, actor, or director name"),
    tag: str | None = Query(None, description="Legacy alias for tags"),
    tags: str | None = Query(None, description="Hashtag filter"),
    genres: str | None = Query(None, description="Comma-separated genre ids"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=500),
):
    try:
        search_title = query or title
        search_tag = tags or tag
        if not any([search_title, search_tag, genres]):
            raise HTTPException(status_code=400, detail="At least one search parameter(query, tags, genres) is required.")

        movies = movie_crud.search_movies(db, title=search_title, tag=search_tag, genres=genres, skip=skip, limit=limit)
        data = [movie_crud.to_movie_search_item(movie) for movie in movies]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"total_results": len(data), "data": data}


# 2026.05.13 박현식
# 탐색 화면 영화 상세 조회를 DB 우선/TMDB 보강 상세 빌더로 위임한다.
@router.get("/movies/{movie_id}")
async def get_movie_detail(
    movie_id: int,
    db: Session = Depends(deps.get_db),
):
    return await movie_detail_crud.build_movie_detail(db, movie_id)


# 2026.05.13 박현식
# legacy 탐색 검색 path를 최신 DB 검색 로직에 연결한다.
@router.get("/search")
def search_db(
    q: str = Query(..., description="Movie title, actor, or director name"),
    sort: str = Query("latest", description="latest, likes, rating"),
    db: Session = Depends(deps.get_db),
):
    movies = movie_crud.search_movies(db, title=q, limit=50)
    data = [movie_crud.to_movie_search_item(movie) for movie in movies]
    cards = [movie_crud.to_explore_card(movie) for movie in data]
    if sort == "rating":
        cards.sort(key=lambda movie: movie["rating"], reverse=True)
    return {"movies": cards}


# 2026.05.13 박현식
# legacy 태그 추천 path를 최신 DB 검색 로직에 연결한다.
@router.get("/recommend")
def get_recommendations(
    tag: str = Query("#대한민국 인기작", description="Selected mood/recommendation tag"),
    page: int = Query(1, ge=1),
    db: Session = Depends(deps.get_db),
):
    skip = (page - 1) * 50
    movies = movie_crud.search_movies(db, tag=tag, skip=skip, limit=50)
    data = [movie_crud.to_movie_search_item(movie) for movie in movies]
    return {"movies": [movie_crud.to_explore_card(movie) for movie in data]}
