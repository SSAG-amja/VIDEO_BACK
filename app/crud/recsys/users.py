from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mapping import PlaylistMovie, UserInteraction, likes
from app.models.playlist import Playlist
from app.models.post import Post
from app.models.reply import Reply
from app.models.user import User


# 2026.06.04 김호영
# 추천 대상 사용자가 존재하는지 확인한다.
def user_exists(db: Session, user_id: int) -> bool:
    return db.scalar(select(User.id).where(User.id == user_id)) is not None


# 2026.06.04 김호영
# worker가 추천을 재계산할 행동 이력이 있는 사용자 id 목록을 수집한다.
def load_worker_user_ids(db: Session) -> list[int]:
    user_ids = set(db.scalars(select(UserInteraction.user_id).distinct()).all())
    user_ids.update(
        db.scalars(
            select(Playlist.user_id)
            .join(PlaylistMovie, PlaylistMovie.playlist_id == Playlist.id)
            .distinct()
        ).all()
    )
    user_ids.update(
        db.scalars(
            select(Post.user_id)
            .where(post_has_recommendable_target())
            .distinct()
        ).all()
    )
    user_ids.update(
        db.scalars(
            select(likes.c.user_id)
            .select_from(likes)
            .join(Post, Post.id == likes.c.post_id)
            .where(post_has_recommendable_target())
            .distinct()
        ).all()
    )
    user_ids.update(
        db.scalars(
            select(Reply.user_id)
            .join(Post, Reply.post_id == Post.id)
            .where(post_has_recommendable_target())
            .distinct()
        ).all()
    )
    return sorted(user_ids)


def post_has_recommendable_target():
    return (
        ((Post.is_playlist.is_(False)) & (Post.movie_id.is_not(None)))
        | ((Post.is_playlist.is_(True)) & (Post.playlist_id.is_not(None)))
    )
