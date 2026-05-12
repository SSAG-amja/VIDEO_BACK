from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.crud import movie as movie_crud
from app.schemas.movie import MovieSearchResponse, recommendedMovie

router = APIRouter()


@router.get("/movies/recommended", response_model=recommendedMovie)
def get_recommended_movies(
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    page: int = Query(1, description="Page number"),
):
    limit = 200
    skip = (page - 1) * limit
    try:
        recommended_movies = movie_crud.get_recommended_movies(db, current_user.id, skip=skip, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"data": recommended_movies}


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
