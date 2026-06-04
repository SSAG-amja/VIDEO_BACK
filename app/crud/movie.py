from sqlalchemy import case, desc, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.models import people as people_model


TAG_HIGH_RATING = "#\ud3c9\uc810 \ub192\uc740 \uba85\uc791"
TAG_ACTION = "#\ub3c4\ud30c\ubbfc \ud3ed\ubc1c \uc561\uc158"
TAG_COMEDY = "#\uac00\ubccd\uac8c \uc6c3\uae30 \uc88b\uc740"
HIGH_RATING_MIN_VOTE_COUNT = 1000
TAG_QUALITY_MIN_VOTE_COUNT = 300
HIGH_RATING_WEIGHTED_AVERAGE = (
    movie_model.Movie.vote_average * func.log(func.greatest(movie_model.Movie.vote_count, 1))
)
DIRECTOR_ROLE = "\uac10\ub3c5\uc791"
ACTOR_ROLE = "\ucd9c\uc5f0\uc791"

TAG_GENRE_MAP = {
    TAG_ACTION: [28, 53],
    TAG_COMEDY: [35],
}
QUALITY_SCORE_TAGS = {TAG_HIGH_RATING, TAG_ACTION, TAG_COMEDY}


# 2026.05.13 박현식
# 사용자의 선호 장르 기반으로 인기 영화 추천 목록을 조회한다.
def get_recommended_movies(db: Session, user_id: int, skip: int = 0, limit: int = 200) -> list[movie_model.Movie]:
    user = user_crud.get_user_with_preferences(db, user_id)
    if not user:
        return []

    genre_ids = [genre.id for genre in user.genres]
    if not genre_ids:
        return []

    stmt = (
        select(
            movie_model.Movie.tmdb_id,
            movie_model.Movie.title_ko,
            movie_model.Movie.poster_path,
            movie_model.Movie.vote_average,
            movie_model.Movie.popularity,
        )
        .join(mapping_model.movie_genres)
        .where(
            mapping_model.movie_genres.c.genre_id.in_(genre_ids),
            movie_model.Movie.poster_path.is_not(None),
        )
        .distinct(movie_model.Movie.popularity)
        .order_by(desc(movie_model.Movie.popularity))
        .offset(skip)
        .limit(limit)
    )
    return db.execute(stmt).mappings().all()


# 2026.05.23 김호영
# 태그 기반 탐색 정렬에서 투표수와 평점을 함께 반영해 품질 점수를 계산한다.
# 2026.05.13 박현식
# 제목, 배우/감독명, 태그, 장르 조건을 조합해 DB 영화 검색 결과를 만든다.
def search_movies(
    db: Session,
    title: str | None = None,
    tag: str | None = None,
    genres: str | None = None,
    skip: int = 0,
    limit: int = 200,
) -> list[movie_model.Movie]:
    title = title.strip() if title else None
    target_count = skip + limit
    rows: list[dict] = []
    seen_tmdb_ids: set[int] = set()
    genre_ids = [int(g.strip()) for g in genres.split(",") if g.strip().isdigit()] if genres else []

    title_relevance = (
        case(
            (movie_model.Movie.title_ko.ilike(title), 1),
            (movie_model.Movie.title.ilike(title), 1),
            (movie_model.Movie.original_title.ilike(title), 1),
            (movie_model.Movie.title_ko.ilike(f"{title}%", escape="\\"), 2),
            (movie_model.Movie.title.ilike(f"{title}%", escape="\\"), 2),
            (movie_model.Movie.original_title.ilike(f"{title}%", escape="\\"), 2),
            else_=3,
        ).label("relevance")
        if title
        else literal(3).label("relevance")
    )
    people_relevance = literal(4).label("relevance")

    # 2026.05.13 박현식
    # 검색 결과에서 공통으로 사용하는 영화 select 구문을 만든다.
    def base_movie_select(relevance):
        return select(
            movie_model.Movie.tmdb_id,
            movie_model.Movie.title_ko,
            movie_model.Movie.poster_path,
            movie_model.Movie.vote_average,
            movie_model.Movie.vote_count,
            movie_model.Movie.popularity,
            HIGH_RATING_WEIGHTED_AVERAGE.label("high_rating_score"),
            relevance,
        ).where(movie_model.Movie.poster_path.is_not(None))

    # 2026.05.13 박현식
    # 장르와 태그 필터를 검색 쿼리에 적용한다.
    def apply_filters(stmt):
        if genre_ids:
            stmt = stmt.join(mapping_model.movie_genres).where(mapping_model.movie_genres.c.genre_id.in_(genre_ids))
        if tag:
            tag_genre_ids = TAG_GENRE_MAP.get(tag)
            if tag_genre_ids:
                stmt = stmt.join(mapping_model.movie_genres).where(mapping_model.movie_genres.c.genre_id.in_(tag_genre_ids))
            if tag in QUALITY_SCORE_TAGS:
                stmt = stmt.where(
                    movie_model.Movie.vote_average.is_not(None),
                    movie_model.Movie.vote_count >= (
                        HIGH_RATING_MIN_VOTE_COUNT if tag == TAG_HIGH_RATING else TAG_QUALITY_MIN_VOTE_COUNT
                    ),
                )
        return stmt

    # 2026.05.13 박현식
    # 검색 결과 정렬과 limit 규칙을 적용한다.
    def finish(stmt, result_limit: int):
        return (
            apply_filters(stmt)
            .distinct()
            .order_by(
                "relevance",
                desc("high_rating_score" if tag in QUALITY_SCORE_TAGS else movie_model.Movie.popularity),
                desc(movie_model.Movie.popularity if tag in (TAG_ACTION, TAG_COMEDY) else movie_model.Movie.vote_average),
                desc(movie_model.Movie.vote_average if tag in (TAG_ACTION, TAG_COMEDY) else movie_model.Movie.vote_count),
            )
            .limit(result_limit)
        )

    # 2026.05.13 박현식
    # 중복 TMDB id를 제거하며 검색 결과를 누적한다.
    def append_movies(movie_rows):
        for movie in movie_rows:
            if movie["tmdb_id"] in seen_tmdb_ids:
                continue
            movie_dict = dict(movie)
            rows.append(movie_dict)
            seen_tmdb_ids.add(movie["tmdb_id"])
            if len(rows) >= target_count:
                break

    # 2026.05.13 박현식
    # 영화 제목, 감독, 배우명을 한 번에 검색해 우선순위대로 합친다.
    def search_title_people_movies():
        search_kw = f"%{title}%"
        title_stmt = base_movie_select(title_relevance).add_columns(literal(None).label("badge")).where(
            or_(
                movie_model.Movie.title_ko.ilike(search_kw),
                movie_model.Movie.title.ilike(search_kw),
                movie_model.Movie.original_title.ilike(search_kw),
            )
        )

        director_stmt = (
            base_movie_select(people_relevance)
            .add_columns(func.concat(people_model.People.name_ko, " ", DIRECTOR_ROLE).label("badge"))
            .join(mapping_model.movie_directors, mapping_model.movie_directors.c.movie_id == movie_model.Movie.id)
            .join(people_model.People, mapping_model.movie_directors.c.director_id == people_model.People.id)
            .where(or_(people_model.People.name.ilike(search_kw), people_model.People.name_ko.ilike(search_kw)))
        )

        actor_stmt = (
            base_movie_select(literal(5).label("relevance"))
            .add_columns(func.concat(people_model.People.name_ko, " ", ACTOR_ROLE).label("badge"))
            .join(mapping_model.MovieActor, mapping_model.MovieActor.movie_id == movie_model.Movie.id)
            .join(people_model.People, mapping_model.MovieActor.actor_id == people_model.People.id)
            .where(or_(people_model.People.name.ilike(search_kw), people_model.People.name_ko.ilike(search_kw)))
        )

        combined = union_all(
            finish(title_stmt, target_count),
            finish(director_stmt, target_count),
            finish(actor_stmt, target_count),
        ).subquery()

        stmt = (
            select(combined)
            .order_by(combined.c.relevance, desc(combined.c.popularity))
            .limit(target_count * 3)
        )
        append_movies(db.execute(stmt).mappings().all())

    if title:
        search_title_people_movies()
        return rows[skip:target_count]

    stmt = finish(base_movie_select(title_relevance), target_count)
    return db.execute(stmt).mappings().all()[skip:target_count]


# 2026.05.13 박현식
# DB 검색 row를 프론트 공통 영화 카드 DTO로 변환한다.
def to_movie_search_item(movie: dict) -> dict:
    return {
        "movie_id": movie.get("tmdb_id"),
        "movie_title": movie.get("title_ko") or "",
        "poster_path": movie.get("poster_path"),
        "vote_average": movie.get("vote_average"),
        "popularity": movie.get("popularity"),
        "badge": movie.get("badge"),
    }


# 2026.05.13 박현식
# legacy explore 화면에서 쓰는 카드 shape으로 영화 DTO를 변환한다.
def to_explore_card(movie: dict) -> dict:
    poster_path = movie.get("poster_path")
    return {
        "id": str(movie.get("movie_id") or movie.get("tmdb_id")),
        "title": movie.get("movie_title") or movie.get("title_ko") or "",
        "rating": round(float(movie.get("vote_average") or 0), 1),
        "image": f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None,
        "poster_path": poster_path,
        "badge": movie.get("badge"),
    }


# 2026.05.23 김호영
# VIDEO_RECSYS가 반환한 내부 movie id 순서를 유지해 프론트 영화 카드 DTO를 조회한다.
# 2026.05.22 VIDEO_RECSYS가 반환한 내부 movie id 순서를 유지해 프론트 영화 카드 DTO를 조회한다.
def get_movies_by_internal_ids_preserve_order(db: Session, movie_ids: list[int]) -> list[dict]:
    if not movie_ids:
        return []

    rows = db.execute(
        select(
            movie_model.Movie.id,
            movie_model.Movie.tmdb_id,
            movie_model.Movie.title_ko,
            movie_model.Movie.poster_path,
            movie_model.Movie.vote_average,
            movie_model.Movie.popularity,
        ).where(movie_model.Movie.id.in_(movie_ids))
    ).mappings().all()

    movie_by_id = {row["id"]: row for row in rows}
    return [movie_by_id[movie_id] for movie_id in movie_ids if movie_id in movie_by_id]
