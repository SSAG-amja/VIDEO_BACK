from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud import interaction as interaction_crud
from app.crud.movie_summary import movie_summary
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.models import playlist as playlist_model
from app.schemas import playlist as playlist_schema


# 2026.05.13 박현식
# 플레이리스트 요약, 전체 영화 수, 썸네일용 영화 목록을 DTO로 변환한다.
def playlist_summary(
    db: Session,
    playlist: playlist_model.Playlist,
    movie_limit: int | None = 5,
) -> playlist_schema.PlaylistSummary:
    stmt = (
        select(movie_model.Movie)
        .join(mapping_model.PlaylistMovie, mapping_model.PlaylistMovie.movie_id == movie_model.Movie.id)
        .where(mapping_model.PlaylistMovie.playlist_id == playlist.id)
        .order_by(mapping_model.PlaylistMovie.created_at.desc())
    )
    if movie_limit is not None:
        stmt = stmt.limit(movie_limit)
    movies = db.execute(stmt).scalars().all()
    movie_count = db.scalar(
        select(func.count())
        .select_from(mapping_model.PlaylistMovie)
        .where(mapping_model.PlaylistMovie.playlist_id == playlist.id)
    ) or 0

    return playlist_schema.PlaylistSummary(
        playlist_id=playlist.id,
        playlist_title=playlist.title,
        playlist_is_public=playlist.is_public,
        movie_count=movie_count,
        movies=[movie_summary(movie) for movie in movies],
    )


# 2026.05.13 박현식
# 현재 사용자가 소유한 플레이리스트를 조회한다.
def get_owned_playlist(db: Session, playlist_id: int, user_id: int) -> playlist_model.Playlist:
    playlist = db.scalar(
        select(playlist_model.Playlist).where(
            playlist_model.Playlist.id == playlist_id,
            playlist_model.Playlist.user_id == user_id,
        )
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


# 2026.05.13 박현식
# 현재 사용자의 플레이리스트 목록을 조회한다.
def get_playlists(db: Session, user_id: int) -> playlist_schema.PlaylistListResponse:
    playlists = db.execute(
        select(playlist_model.Playlist)
        .where(playlist_model.Playlist.user_id == user_id)
        .order_by(playlist_model.Playlist.id.desc())
    ).scalars().all()

    return playlist_schema.PlaylistListResponse(
        total=len(playlists),
        data=[playlist_summary(db, playlist) for playlist in playlists],
    )


# 2026.05.13 박현식
# 새 플레이리스트를 생성한다.
def create_playlist(
    db: Session,
    user_id: int,
    request: playlist_schema.PlaylistCreateRequest,
) -> playlist_schema.PlaylistSummary:
    title = request.playlist_title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="playlist_title is required")

    playlist = playlist_model.Playlist(
        user_id=user_id,
        title=title,
        is_public=request.playlist_is_public,
    )
    db.add(playlist)
    db.commit()
    db.refresh(playlist)
    return playlist_summary(db, playlist)


# 2026.05.13 박현식
# 플레이리스트 제목 또는 공개 여부를 수정한다.
def update_playlist(
    db: Session,
    user_id: int,
    request: playlist_schema.PlaylistUpdateRequest,
) -> playlist_schema.PlaylistSummary:
    playlist = get_owned_playlist(db, request.playlist_id, user_id)

    if request.playlist_title is not None:
        title = request.playlist_title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="playlist_title cannot be empty")
        playlist.title = title

    if request.playlist_is_public is not None:
        playlist.is_public = request.playlist_is_public

    db.commit()
    db.refresh(playlist)
    return playlist_summary(db, playlist)


# 2026.05.13 박현식
# 현재 사용자가 소유한 플레이리스트 하나를 삭제한다.
def delete_playlist(db: Session, user_id: int, playlist_id: int) -> int:
    playlist = get_owned_playlist(db, playlist_id, user_id)
    db.delete(playlist)
    db.commit()
    return playlist_id


# 2026.05.13 박현식
# 현재 사용자의 모든 플레이리스트를 삭제한다.
def delete_all_playlists(db: Session, user_id: int) -> int:
    playlists = db.execute(
        select(playlist_model.Playlist).where(playlist_model.Playlist.user_id == user_id)
    ).scalars().all()

    for playlist in playlists:
        db.delete(playlist)
    db.commit()
    return len(playlists)


# 2026.05.13 박현식
# 특정 플레이리스트에 담긴 영화 목록을 조회한다.
def get_playlist_movies(
    db: Session,
    user_id: int,
    playlist_id: int,
) -> playlist_schema.MovieListResponse:
    get_owned_playlist(db, playlist_id, user_id)
    movies = db.execute(
        select(movie_model.Movie)
        .join(mapping_model.PlaylistMovie, mapping_model.PlaylistMovie.movie_id == movie_model.Movie.id)
        .where(mapping_model.PlaylistMovie.playlist_id == playlist_id)
        .order_by(mapping_model.PlaylistMovie.created_at.asc())
    ).scalars().all()

    return playlist_schema.MovieListResponse(
        total=len(movies),
        data=[movie_summary(movie) for movie in movies],
    )


# 2026.05.13 박현식
# 특정 플레이리스트에 영화를 중복 없이 추가한다.
def add_playlist_movie(
    db: Session,
    user_id: int,
    playlist_id: int,
    request: playlist_schema.PlaylistMovieRequest,
) -> playlist_schema.MovieSummary:
    get_owned_playlist(db, playlist_id, user_id)
    movie = interaction_crud.get_movie_by_tmdb_id(db, request.movie_id)

    exists = db.get(mapping_model.PlaylistMovie, {"playlist_id": playlist_id, "movie_id": movie.id})
    if not exists:
        db.add(mapping_model.PlaylistMovie(playlist_id=playlist_id, movie_id=movie.id))
        db.commit()

    return movie_summary(movie)


# 2026.05.13 박현식
# 특정 플레이리스트에서 영화 하나를 삭제한다.
def delete_playlist_movie(
    db: Session,
    user_id: int,
    playlist_id: int,
    movie_id: int,
) -> playlist_schema.MovieSummary:
    get_owned_playlist(db, playlist_id, user_id)
    movie = interaction_crud.get_movie_by_tmdb_id(db, movie_id)

    playlist_movie = db.get(mapping_model.PlaylistMovie, {"playlist_id": playlist_id, "movie_id": movie.id})
    if playlist_movie:
        db.delete(playlist_movie)
        db.commit()

    return movie_summary(movie)


# 2026.05.13 박현식
# 특정 플레이리스트의 모든 영화를 삭제한다.
def delete_all_playlist_movies(db: Session, user_id: int, playlist_id: int) -> int:
    get_owned_playlist(db, playlist_id, user_id)
    playlist_movies = db.execute(
        select(mapping_model.PlaylistMovie).where(mapping_model.PlaylistMovie.playlist_id == playlist_id)
    ).scalars().all()

    for playlist_movie in playlist_movies:
        db.delete(playlist_movie)
    db.commit()
    return len(playlist_movies)
