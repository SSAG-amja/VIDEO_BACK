from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.models import playlist as playlist_model
from app.schemas import action as action_schema


# 2026.05.13 박현식
# TMDB id 기준으로 내부 Movie row를 조회한다.
def get_movie_by_tmdb_id(db: Session, movie_id: int) -> movie_model.Movie:
    movie = db.scalar(select(movie_model.Movie).where(movie_model.Movie.tmdb_id == movie_id))
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie


# 2026.05.13 박현식
# 사용자-영화 interaction row가 없으면 새로 생성한다.
def get_or_create_interaction(
    db: Session,
    user_id: int,
    movie_id: int,
) -> mapping_model.UserInteraction:
    interaction = db.get(mapping_model.UserInteraction, {"user_id": user_id, "movie_id": movie_id})
    if interaction:
        return interaction

    interaction = mapping_model.UserInteraction(user_id=user_id, movie_id=movie_id)
    db.add(interaction)
    db.flush()
    return interaction


# 2026.05.13 박현식
# 사용자의 플레이리스트 중 해당 영화를 저장한 항목이 있는지 확인한다.
def is_saved(db: Session, user_id: int, movie_id: int) -> bool:
    return db.scalar(
        select(mapping_model.PlaylistMovie)
        .join(playlist_model.Playlist, playlist_model.Playlist.id == mapping_model.PlaylistMovie.playlist_id)
        .where(
            playlist_model.Playlist.user_id == user_id,
            mapping_model.PlaylistMovie.movie_id == movie_id,
        )
    ) is not None


# 2026.05.13 박현식
# 소유한 플레이리스트에 영화를 중복 없이 추가한다.
def add_movie_to_playlist(db: Session, user_id: int, movie_id: int, playlist_id: int) -> None:
    playlist = db.scalar(
        select(playlist_model.Playlist).where(
            playlist_model.Playlist.id == playlist_id,
            playlist_model.Playlist.user_id == user_id,
        )
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    exists = db.get(mapping_model.PlaylistMovie, {"playlist_id": playlist_id, "movie_id": movie_id})
    if exists:
        return

    db.add(mapping_model.PlaylistMovie(playlist_id=playlist_id, movie_id=movie_id))


# 2026.05.13 박현식
# pin/pass/watched/saved 액션을 user_interactions 또는 playlist_movies에 저장한다.
def update_movie_interaction(
    db: Session,
    user_id: int,
    tmdb_movie_id: int,
    request: action_schema.InteractionUpdateRequest,
) -> action_schema.InteractionUpdateResponse:
    movie = get_movie_by_tmdb_id(db, tmdb_movie_id)
    interaction = get_or_create_interaction(db, user_id, movie.id)

    if request.action_type == "pin":
        interaction.is_pinned = True
        interaction.pinned_at = func.now()
        interaction.is_passed = False
        interaction.passed_at = None
    elif request.action_type == "passed":
        interaction.is_passed = True
        interaction.passed_at = func.now()
        interaction.is_pinned = False
        interaction.pinned_at = None
    elif request.action_type == "watched":
        interaction.is_watched = True
        interaction.watched_at = func.now()
    elif request.action_type == "saved":
        if request.playlist_id is None:
            raise HTTPException(status_code=422, detail="playlist_id is required.")
        add_movie_to_playlist(db, user_id, movie.id, request.playlist_id)

    db.commit()
    db.refresh(interaction)

    return action_schema.InteractionUpdateResponse(
        data=action_schema.InteractionState(
            movie_id=movie.tmdb_id,
            is_pinned=interaction.is_pinned,
            is_passed=interaction.is_passed,
            is_watched=interaction.is_watched,
            is_saved=is_saved(db, user_id, movie.id),
        )
    )
