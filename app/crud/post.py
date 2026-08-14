from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import hashtag as hashtag_model
from app.models import movie as movie_model
from app.models import playlist as playlist_model
from app.models import poll as poll_model
from app.models import post as post_model
from app.models import reply as reply_model
from app.models import mapping as mapping_model
from app.models import user as user_model
from app.schemas import post as post_schema


# 2026.05.18 박현식
# 게시물과 댓글 작성 시각을 현재 기준 경과 분 단위로 변환한다.
def elapsed_minutes(created_at: datetime | None) -> int:
    if created_at is None:
        return 0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(int((datetime.now(timezone.utc) - created_at).total_seconds() // 60), 0)


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 투표(poll) 및 투표 선택지/투표자 관계 eager load 추가
# 게시물 응답에 필요한 작성자, 태그, 좋아요, 댓글, 영화, 플레이리스트, 투표 관계를 한 번에 로드한다.
def post_query():
    return select(post_model.Post).options(
        joinedload(post_model.Post.user),
        joinedload(post_model.Post.hashtags),
        joinedload(post_model.Post.liked_by),
        joinedload(post_model.Post.replies).joinedload(reply_model.Reply.user),
        joinedload(post_model.Post.movie).joinedload(movie_model.Movie.genres),
        joinedload(post_model.Post.movie).joinedload(movie_model.Movie.directors),
        joinedload(post_model.Post.movie)
        .joinedload(movie_model.Movie.movie_actors)
        .joinedload(mapping_model.MovieActor.actor),
        joinedload(post_model.Post.movie)
        .joinedload(movie_model.Movie.movie_otts)
        .joinedload(mapping_model.MovieOtt.ott),
        joinedload(post_model.Post.playlist)
        .joinedload(playlist_model.Playlist.playlist_movies)
        .joinedload(mapping_model.PlaylistMovie.movie),
        joinedload(post_model.Post.poll)
        .joinedload(poll_model.Poll.options)
        .joinedload(poll_model.PollOption.votes),
        joinedload(post_model.Post.poll).joinedload(poll_model.Poll.votes),
    )


# 2026.05.18 박현식
# 게시물 id로 상세 데이터를 조회하고 없으면 404 예외를 발생시킨다.
def get_post_or_404(db: Session, post_id: int) -> post_model.Post:
    post = db.execute(post_query().where(post_model.Post.id == post_id)).unique().scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


# 2026.05.18 박현식
# 입력 태그를 정규화하고 기존 해시태그를 재사용하거나 새 해시태그를 생성한다.
def get_or_create_hashtag(db: Session, raw_name: str | None) -> hashtag_model.Hashtag | None:
    if raw_name is None:
        return None
    name = raw_name.strip()
    if not name:
        return None
    if not name.startswith("#"):
        name = f"#{name}"

    hashtag = db.execute(
        select(hashtag_model.Hashtag).where(hashtag_model.Hashtag.name == name)
    ).scalar_one_or_none()
    if hashtag:
        return hashtag

    hashtag = hashtag_model.Hashtag(name=name)
    db.add(hashtag)
    db.flush()
    return hashtag


# 2026.08.14 임재준
# 투표 ORM 객체를 사용자 투표 여부 및 선택지별 득표수가 포함된 DTO로 변환한다.
def to_poll_summary(poll: poll_model.Poll | None, current_user_id: int) -> post_schema.PollSummary | None:
    if not poll:
        return None

    user_voted_option_id = None
    for vote in poll.votes:
        if vote.user_id == current_user_id:
            user_voted_option_id = vote.option_id
            break

    options_summary = [
        post_schema.PollOptionSummary(
            id=opt.id,
            text=opt.text,
            votes=len(opt.votes),
        )
        for opt in poll.options
    ]

    return post_schema.PollSummary(
        id=poll.id,
        question=poll.question,
        options=options_summary,
        total_votes=len(poll.votes),
        user_voted_option_id=user_voted_option_id,
        is_closed=poll.is_closed,
    )


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 대댓글 및 댓글 좋아요 정보 포함 DTO 반환
# 댓글 ORM 객체를 작성자, 부모댓글, 좋아요 및 내 댓글 여부가 포함된 응답 요약으로 변환한다.
def to_reply_summary(reply: reply_model.Reply, current_user_id: int) -> post_schema.ReplySummary:
    liked_by = getattr(reply, "liked_by", [])
    likes_count = len(liked_by) if liked_by is not None else getattr(reply, "likes_count", 0)
    is_liked = any(u.id == current_user_id for u in liked_by) if liked_by else False

    parent_id = getattr(reply, "parent_id", None)
    parent_reply = getattr(reply, "parent", None)
    reply_to_user = parent_reply.user.nickname if (parent_reply and parent_reply.user) else None

    return post_schema.ReplySummary(
        nickname=reply.user.nickname if reply.user else None,
        reply_id=reply.id,
        reply_content=reply.content,
        reply_elapsed_time=elapsed_minutes(reply.created_at),
        reply_is_mine=reply.user_id == current_user_id,
        parent_id=parent_id,
        reply_to_user=reply_to_user,
        reply_likes=likes_count,
        reply_is_liked=is_liked,
    )


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 투표(poll) 정보 포함 DTO 변환
# 게시물 ORM 객체를 커뮤니티 화면에서 사용하는 영화/플레이리스트/댓글/투표 포함 DTO로 변환한다.
def to_post_response(
    db: Session,
    post: post_model.Post,
    current_user_id: int,
    include_replies: bool = False,
) -> post_schema.PostResponse:
    movie = post.movie
    playlist = post.playlist

    director = None
    genres = []
    actors = []
    otts = []
    if movie:
        director = movie.directors[0].name_ko if movie.directors else None
        genres = [
            post_schema.GenreSummary(genre_id=genre.id, name=genre.name_ko or genre.name)
            for genre in movie.genres
        ]
        actors = [
            post_schema.ActorSummary(
                actor_name=movie_actor.actor.name_ko or movie_actor.actor.name,
                actor_profile=None,
            )
            for movie_actor in movie.movie_actors
            if movie_actor.actor
        ]
        otts = [
            post_schema.OttSummary(
                ott_id=movie_ott.ott.id,
                ott_name=movie_ott.ott.name_ko or movie_ott.ott.name,
                type="streaming" if movie_ott.is_streaming else "rent" if movie_ott.is_rent else "buy",
            )
            for movie_ott in movie.movie_otts
            if movie_ott.ott
        ]

    playlist_movies = []
    if playlist:
        playlist_movies = [
            post_schema.PostPlaylistMovieSummary(
                movie_id=playlist_movie.movie_id,
                movie_title=(
                    playlist_movie.movie.title_ko or playlist_movie.movie.title
                    if playlist_movie.movie
                    else None
                ),
                poster_path=playlist_movie.movie.poster_path if playlist_movie.movie else None,
            )
            for playlist_movie in playlist.playlist_movies
        ]

    replies = []
    if include_replies:
        replies = [
            to_reply_summary(reply, current_user_id)
            for reply in sorted(post.replies, key=lambda item: item.created_at)
        ]

    return post_schema.PostResponse(
        post_id=post.id,
        post_elapsed_time=elapsed_minutes(post.created_at),
        posting_time=elapsed_minutes(post.created_at),
        is_playlist=post.is_playlist,
        nickname=post.user.nickname if post.user else None,
        movie_id=post.movie_id,
        movie_title=(movie.title_ko or movie.title) if movie else None,
        poster_path=movie.poster_path if movie else None,
        director=director,
        genres=genres,
        actors=actors,
        otts=otts,
        playlist_id=post.playlist_id,
        playlist_title=playlist.title if playlist else None,
        movies=playlist_movies,
        post_title=post.post_title,
        post_content=post.content,
        hashtags=[hashtag.name for hashtag in post.hashtags],
        post_likes=len(post.liked_by),
        post_replies=len(post.replies),
        post_is_mine=post.user_id == current_user_id,
        post_is_liked=any(user.id == current_user_id for user in post.liked_by),
        replies=replies,
        poll=to_poll_summary(post.poll, current_user_id),
    )


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 게시물 생성 시 첨부된 투표 및 선택지 함께 생성
# 영화 또는 내 공개 플레이리스트를 대상으로 게시물을 생성하고 해시태그 및 투표 관계를 저장한다.
def create_post(
    db: Session,
    user_id: int,
    request: post_schema.PostCreateRequest,
) -> post_schema.PostResponse:
    if request.is_playlist:
        playlist = db.get(playlist_model.Playlist, request.playlist_id)
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if playlist.user_id != user_id:
            raise HTTPException(status_code=403, detail="Cannot share another user's playlist")
        movie_id = None
        playlist_id = request.playlist_id
    else:
        movie = db.get(movie_model.Movie, request.movie_id)
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        movie_id = request.movie_id
        playlist_id = None

    post_title = request.post_title.strip()
    post_content = request.post_content.strip()
    if not post_title:
        raise HTTPException(status_code=422, detail="post_title cannot be empty")
    if not post_content:
        raise HTTPException(status_code=422, detail="post_content cannot be empty")

    post = post_model.Post(
        user_id=user_id,
        movie_id=movie_id,
        playlist_id=playlist_id,
        is_playlist=request.is_playlist,
        post_title=post_title,
        content=post_content,
        hashtags=[
            hashtag
            for hashtag in (get_or_create_hashtag(db, name) for name in request.hashtags)
            if hashtag is not None
        ],
    )
    db.add(post)
    db.flush()

    # 투표 첨부 데이터가 있는 경우 Poll 및 PollOption 생성
    if request.poll and request.poll.options:
        valid_options = [opt.strip() for opt in request.poll.options if opt.strip()]
        if len(valid_options) >= 2:
            new_poll = poll_model.Poll(
                post_id=post.id,
                question=request.poll.question.strip() if request.poll.question else None,
            )
            db.add(new_poll)
            db.flush()

            for opt_text in valid_options:
                db.add(poll_model.PollOption(poll_id=new_poll.id, text=opt_text))

    db.commit()
    return to_post_response(db, get_post_or_404(db, post.id), user_id, include_replies=True)


# app/crud/post.py 내 vote_poll 함수 수정

# 2026.08.14 임재준
# 특정 게시글의 투표에 참여하거나, 이미 투표한 항목을 다시 누르면 투표를 취소하고 최신 결과를 반환한다.
def vote_poll(
    db: Session,
    post_id: int,
    option_id: int,
    current_user: user_model.User,
) -> post_schema.PollVoteResponse:
    post = get_post_or_404(db, post_id)
    if not post.poll:
        raise HTTPException(status_code=404, detail="Poll not found on this post")
    if post.poll.is_closed:
        raise HTTPException(status_code=400, detail="This poll is closed")

    # 선택지가 해당 투표의 것인지 검증
    target_option = db.scalar(
        select(poll_model.PollOption).where(
            poll_model.PollOption.id == option_id,
            poll_model.PollOption.poll_id == post.poll.id,
        )
    )
    if not target_option:
        raise HTTPException(status_code=404, detail="Poll option not found")

    # 기존 투표 내역 확인
    existing_vote = db.scalar(
        select(poll_model.PollVote).where(
            poll_model.PollVote.poll_id == post.poll.id,
            poll_model.PollVote.user_id == current_user.id,
        )
    )

    if existing_vote:
        if existing_vote.option_id == option_id:
            # 2026.08.14 임재준: 동일한 항목을 다시 누르면 투표 취소
            db.delete(existing_vote)
            message = "Vote cancelled successfully."
        else:
            # 다른 항목을 누르면 선택지 변경
            existing_vote.option_id = option_id
            message = "Vote changed successfully."
    else:
        # 최초 투표 등록
        new_vote = poll_model.PollVote(
            poll_id=post.poll.id,
            option_id=option_id,
            user_id=current_user.id,
        )
        db.add(new_vote)
        message = "Vote registered successfully."

    db.commit()
    updated_post = get_post_or_404(db, post_id)
    return post_schema.PollVoteResponse(
        message=message,
        poll=to_poll_summary(updated_post.poll, current_user.id),
    )


# 2026.05.18 박현식
# 전체 커뮤니티 게시물을 최신순으로 조회하고 현재 사용자의 좋아요/소유 상태를 포함한다.
def get_posts(db: Session, current_user_id: int) -> post_schema.PostListResponse:
    posts = db.execute(post_query().order_by(post_model.Post.created_at.desc())).unique().scalars().all()
    return post_schema.PostListResponse(
        data=[to_post_response(db, post, current_user_id) for post in posts]
    )


# 2026.05.18 박현식
# 단일 게시물을 조회하고 댓글 목록까지 포함한 상세 DTO를 반환한다.
def get_post(db: Session, post_id: int, current_user_id: int) -> post_schema.PostResponse:
    return to_post_response(db, get_post_or_404(db, post_id), current_user_id, include_replies=True)


# 2026.05.18 박현식
# 게시물에 속한 댓글을 조회하고 없으면 404 예외를 발생시킨다.
def get_reply_or_404(db: Session, post_id: int, reply_id: int) -> reply_model.Reply:
    reply = db.scalar(
        select(reply_model.Reply)
        .options(joinedload(reply_model.Reply.user))
        .where(reply_model.Reply.id == reply_id, reply_model.Reply.post_id == post_id)
    )
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    return reply


# 2026.05.18 박현식
# 2026.08.14 임재준 수정: 대댓글(parent_id) 유효성 검증 및 생성 지원
# 댓글 내용을 검증한 뒤 현재 사용자 댓글을 생성하고 요약 DTO로 반환한다.
def create_reply(
    db: Session,
    post_id: int,
    current_user: user_model.User,
    request: post_schema.ReplyRequest,
) -> post_schema.ReplySummary:
    get_post_or_404(db, post_id)
    content = request.reply_content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="reply_content cannot be empty")

    parent_id = None
    if request.parent_id is not None:
        parent_reply = get_reply_or_404(db, post_id, request.parent_id)
        parent_id = parent_reply.id

    reply_kwargs = {
        "user_id": current_user.id,
        "post_id": post_id,
        "content": content,
    }
    if hasattr(reply_model.Reply, "parent_id"):
        reply_kwargs["parent_id"] = parent_id

    reply = reply_model.Reply(**reply_kwargs)
    db.add(reply)
    db.commit()
    return to_reply_summary(get_reply_or_404(db, post_id, reply.id), current_user.id)


# 2026.05.18 박현식
# 내 댓글인지 검증한 뒤 댓글 내용을 수정하고 갱신된 요약 DTO를 반환한다.
def update_reply(
    db: Session,
    post_id: int,
    reply_id: int,
    current_user: user_model.User,
    request: post_schema.ReplyRequest,
) -> post_schema.ReplySummary:
    reply = get_reply_or_404(db, post_id, reply_id)
    if reply.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot update another user's reply")

    content = request.reply_content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="reply_content cannot be empty")
    reply.content = content

    db.commit()
    return to_reply_summary(get_reply_or_404(db, post_id, reply_id), current_user.id)


# 2026.05.18 박현식
# 내 댓글인지 검증한 뒤 댓글을 삭제하고 삭제된 댓글 id를 반환한다.
def delete_reply(db: Session, post_id: int, reply_id: int, current_user: user_model.User) -> int:
    reply = get_reply_or_404(db, post_id, reply_id)
    if reply.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete another user's reply")

    db.delete(reply)
    db.commit()
    return reply_id


# 2026.08.14 임재준
# 댓글 좋아요를 멱등하게 추가하고 서버 기준 댓글 좋아요 수와 상태를 반환한다.
def like_reply(
    db: Session,
    post_id: int,
    reply_id: int,
    current_user: user_model.User,
) -> post_schema.ReplyLikeResponse:
    reply = get_reply_or_404(db, post_id, reply_id)

    if hasattr(mapping_model, "reply_likes"):
        exists = db.execute(
            select(mapping_model.reply_likes).where(
                mapping_model.reply_likes.c.user_id == current_user.id,
                mapping_model.reply_likes.c.reply_id == reply_id,
            )
        ).first()

        if not exists:
            db.execute(
                mapping_model.reply_likes.insert().values(user_id=current_user.id, reply_id=reply_id)
            )
            db.commit()

    reply = get_reply_or_404(db, post_id, reply_id)
    liked_by = getattr(reply, "liked_by", [])
    likes_count = len(liked_by) if liked_by is not None else getattr(reply, "likes_count", 0)

    return post_schema.ReplyLikeResponse(
        message="Reply liked.",
        reply_id=reply.id,
        reply_likes=likes_count,
        reply_is_liked=True,
    )


# 2026.08.14 임재준
# 댓글 좋아요를 멱등하게 제거하고 서버 기준 댓글 좋아요 수와 상태를 반환한다.
def unlike_reply(
    db: Session,
    post_id: int,
    reply_id: int,
    current_user: user_model.User,
) -> post_schema.ReplyLikeResponse:
    reply = get_reply_or_404(db, post_id, reply_id)

    if hasattr(mapping_model, "reply_likes"):
        db.execute(
            mapping_model.reply_likes.delete().where(
                mapping_model.reply_likes.c.user_id == current_user.id,
                mapping_model.reply_likes.c.reply_id == reply_id,
            )
        )
        db.commit()

    reply = get_reply_or_404(db, post_id, reply_id)
    liked_by = getattr(reply, "liked_by", [])
    likes_count = len(liked_by) if liked_by is not None else getattr(reply, "likes_count", 0)

    return post_schema.ReplyLikeResponse(
        message="Reply unliked.",
        reply_id=reply.id,
        reply_likes=likes_count,
        reply_is_liked=False,
    )


# 2026.05.18 박현식
# 게시물 좋아요를 멱등하게 추가하고 서버 기준 좋아요 수와 상태를 반환한다.
def like_post(db: Session, post_id: int, current_user: user_model.User) -> post_schema.PostLikeResponse:
    post = get_post_or_404(db, post_id)
    exists = db.execute(
        select(mapping_model.likes).where(
            mapping_model.likes.c.user_id == current_user.id,
            mapping_model.likes.c.post_id == post_id,
        )
    ).first()

    if not exists:
        db.execute(
            mapping_model.likes.insert().values(user_id=current_user.id, post_id=post_id)
        )
        db.commit()
        post = get_post_or_404(db, post_id)

    return post_schema.PostLikeResponse(
        message="Post liked.",
        post_id=post.id,
        post_likes=len(post.liked_by),
        post_is_liked=True,
    )


# 2026.05.18 박현식
# 게시물 좋아요를 멱등하게 제거하고 서버 기준 좋아요 수와 상태를 반환한다.
def unlike_post(db: Session, post_id: int, current_user: user_model.User) -> post_schema.PostLikeResponse:
    post = get_post_or_404(db, post_id)
    db.execute(
        mapping_model.likes.delete().where(
            mapping_model.likes.c.user_id == current_user.id,
            mapping_model.likes.c.post_id == post_id,
        )
    )
    db.commit()
    post = get_post_or_404(db, post_id)

    return post_schema.PostLikeResponse(
        message="Post unliked.",
        post_id=post.id,
        post_likes=len(post.liked_by),
        post_is_liked=False,
    )


# 2026.05.18 박현식
# 내 게시물인지 검증한 뒤 제목, 내용, 해시태그 변경 사항을 저장한다.
def update_post(
    db: Session,
    post_id: int,
    current_user: user_model.User,
    request: post_schema.PostUpdateRequest,
) -> post_schema.PostResponse:
    post = get_post_or_404(db, post_id)
    if post.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot update another user's post")

    if request.post_title is not None:
        post_title = request.post_title.strip()
        if not post_title:
            raise HTTPException(status_code=422, detail="post_title cannot be empty")
        post.post_title = post_title

    if request.post_content is not None:
        content = request.post_content.strip()
        if not content:
            raise HTTPException(status_code=422, detail="post_content cannot be empty")
        post.content = content

    if request.hashtags is not None:
        post.hashtags = [
            hashtag
            for hashtag in (get_or_create_hashtag(db, name) for name in request.hashtags)
            if hashtag is not None
        ]

    db.commit()
    return to_post_response(db, get_post_or_404(db, post_id), current_user.id, include_replies=True)


# 2026.05.18 박현식
# 내 게시물인지 검증한 뒤 게시물을 삭제하고 삭제된 게시물 id를 반환한다.
def delete_post(db: Session, post_id: int, current_user: user_model.User) -> int:
    post = db.get(post_model.Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete another user's post")
    db.delete(post)
    db.commit()
    return post_id