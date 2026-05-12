from sqlalchemy import case, desc, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.models import mapping as mapping_model
from app.models import movie as movie_model
from app.models import people as people_model


TAG_HIGH_RATING = "#\ud3c9\uc810 \ub192\uc740 \uba85\uc791"
TAG_ACTION = "#\ub3c4\ud30c\ubbfc \ud3ed\ubc1c \uc561\uc158"
TAG_COMEDY = "#\uac00\ubccd\uac8c \uc6c3\uae30 \uc88b\uc740"
DIRECTOR_ROLE = "\uac10\ub3c5\uc791"
ACTOR_ROLE = "\ucd9c\uc5f0\uc791"

TAG_GENRE_MAP = {
    TAG_ACTION: [28, 53],
    TAG_COMEDY: [35],
}


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

    def base_movie_select(relevance):
        return select(
            movie_model.Movie.tmdb_id,
            movie_model.Movie.title_ko,
            movie_model.Movie.poster_path,
            movie_model.Movie.vote_average,
            movie_model.Movie.popularity,
            relevance,
        ).where(movie_model.Movie.poster_path.is_not(None))

    def apply_filters(stmt):
        if genre_ids:
            stmt = stmt.join(mapping_model.movie_genres).where(mapping_model.movie_genres.c.genre_id.in_(genre_ids))
        if tag:
            tag_genre_ids = TAG_GENRE_MAP.get(tag)
            if tag_genre_ids:
                stmt = stmt.join(mapping_model.movie_genres).where(mapping_model.movie_genres.c.genre_id.in_(tag_genre_ids))
            if tag == TAG_HIGH_RATING:
                stmt = stmt.where(movie_model.Movie.vote_average.is_not(None))
        return stmt

    def finish(stmt, result_limit: int):
        return (
            apply_filters(stmt)
            .distinct()
            .order_by(
                "relevance",
                desc(movie_model.Movie.vote_average if tag == TAG_HIGH_RATING else movie_model.Movie.popularity),
                desc(movie_model.Movie.popularity),
            )
            .limit(result_limit)
        )

    def append_movies(movie_rows):
        for movie in movie_rows:
            if movie["tmdb_id"] in seen_tmdb_ids:
                continue
            movie_dict = dict(movie)
            rows.append(movie_dict)
            seen_tmdb_ids.add(movie["tmdb_id"])
            if len(rows) >= target_count:
                break

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


def to_movie_search_item(movie: dict) -> dict:
    return {
        "movie_id": movie.get("tmdb_id"),
        "movie_title": movie.get("title_ko") or "",
        "poster_path": movie.get("poster_path"),
        "vote_average": movie.get("vote_average"),
        "popularity": movie.get("popularity"),
        "badge": movie.get("badge"),
    }


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
