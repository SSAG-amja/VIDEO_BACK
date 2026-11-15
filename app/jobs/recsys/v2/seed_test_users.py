from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models.genre import Genre
from app.models.mapping import MovieActor, UserInteraction, user_favorite_movies, user_genres, user_otts
from app.models.movie import Movie
from app.models.ott import Ott
from app.models.user import User


TEST_EMAIL_DOMAIN = "pinlm.test"
TEST_PASSWORD = "Testpass123!"


@dataclass(frozen=True)
class TestUserSpec:
    email: str
    nickname: str
    gender: str
    genre_names: tuple[str, ...]
    favorite_genre_names: tuple[str, ...]
    ott_names: tuple[str, ...] = ()
    pinned_genre_names: tuple[str, ...] = ()
    watched_genre_names: tuple[str, ...] = ()
    passed_genre_names: tuple[str, ...] = ()
    onboarding_completed: bool = True


TEST_USERS = (
    TestUserSpec(
        email=f"v2_action@{TEST_EMAIL_DOMAIN}",
        nickname="v2act",
        gender="M",
        genre_names=("Action", "Thriller", "Science Fiction"),
        favorite_genre_names=("Action", "Thriller"),
        ott_names=("Netflix", "Disney Plus"),
        pinned_genre_names=("Action",),
        watched_genre_names=("Adventure",),
        passed_genre_names=("Romance",),
    ),
    TestUserSpec(
        email=f"v2_romance@{TEST_EMAIL_DOMAIN}",
        nickname="v2rom",
        gender="F",
        genre_names=("Romance", "Drama", "Comedy"),
        favorite_genre_names=("Romance", "Drama"),
        ott_names=("Netflix",),
        pinned_genre_names=("Romance",),
        watched_genre_names=("Comedy",),
        passed_genre_names=("Horror",),
    ),
    TestUserSpec(
        email=f"v2_horror@{TEST_EMAIL_DOMAIN}",
        nickname="v2hor",
        gender="U",
        genre_names=("Horror", "Mystery", "Thriller"),
        favorite_genre_names=("Horror", "Mystery"),
        ott_names=("Amazon Prime Video",),
        pinned_genre_names=("Horror",),
        watched_genre_names=("Thriller",),
        passed_genre_names=("Family",),
    ),
    TestUserSpec(
        email=f"v2_family@{TEST_EMAIL_DOMAIN}",
        nickname="v2fam",
        gender="F",
        genre_names=("Animation", "Family", "Adventure"),
        favorite_genre_names=("Animation", "Family"),
        ott_names=("Disney Plus",),
        pinned_genre_names=("Animation",),
        watched_genre_names=("Family",),
        passed_genre_names=("Crime",),
    ),
    TestUserSpec(
        email=f"v2_empty@{TEST_EMAIL_DOMAIN}",
        nickname="v2empty",
        gender="U",
        genre_names=(),
        favorite_genre_names=(),
        onboarding_completed=False,
    ),
)


def run_seed(db: Session) -> list[dict]:
    results: list[dict] = []
    for spec in TEST_USERS:
        user = upsert_test_user(db, spec)
        clear_user_preferences(db, user.id)
        if spec.onboarding_completed:
            insert_user_genres(db, user.id, spec.genre_names)
            insert_user_otts(db, user.id, spec.ott_names)
            favorite_movie_ids = select_movies_for_genres(db, spec.favorite_genre_names, limit=6)
            insert_user_favorite_movies(db, user.id, favorite_movie_ids)
            insert_interactions(db, user.id, spec)
        user.is_onboarding_completed = spec.onboarding_completed
        db.commit()
        results.append(
            {
                "user_id": user.id,
                "email": user.email,
                "nickname": user.nickname,
                "onboarding": user.is_onboarding_completed,
            }
        )
    return results


def upsert_test_user(db: Session, spec: TestUserSpec) -> User:
    user = db.scalar(select(User).where(User.email == spec.email))
    if user is None:
        user = User(
            email=spec.email,
            hashed_password=get_password_hash(TEST_PASSWORD),
            nickname=spec.nickname,
            birth_date=date(1998, 1, 1),
            gender=spec.gender,
            is_onboarding_completed=spec.onboarding_completed,
        )
        db.add(user)
        db.flush()
        return user

    user.hashed_password = get_password_hash(TEST_PASSWORD)
    user.nickname = spec.nickname
    user.birth_date = date(1998, 1, 1)
    user.gender = spec.gender
    user.deleted_at = None
    user.is_onboarding_completed = spec.onboarding_completed
    db.flush()
    return user


def clear_user_preferences(db: Session, user_id: int) -> None:
    db.execute(delete(UserInteraction).where(UserInteraction.user_id == user_id))
    db.execute(user_favorite_movies.delete().where(user_favorite_movies.c.user_id == user_id))
    db.execute(user_genres.delete().where(user_genres.c.user_id == user_id))
    db.execute(user_otts.delete().where(user_otts.c.user_id == user_id))
    db.flush()


def insert_user_genres(db: Session, user_id: int, genre_names: tuple[str, ...]) -> None:
    genre_ids = list(
        db.scalars(
            select(Genre.id)
            .where(Genre.name.in_(genre_names))
            .order_by(Genre.id)
        )
    )
    for genre_id in genre_ids:
        db.execute(user_genres.insert().values(user_id=user_id, genre_id=genre_id))


def insert_user_otts(db: Session, user_id: int, ott_names: tuple[str, ...]) -> None:
    if not ott_names:
        return
    ott_ids = list(
        db.scalars(
            select(Ott.id)
            .where(Ott.name.in_(ott_names))
            .order_by(Ott.id)
        )
    )
    for ott_id in ott_ids:
        db.execute(user_otts.insert().values(user_id=user_id, ott_id=ott_id))


def insert_user_favorite_movies(db: Session, user_id: int, movie_ids: list[int]) -> None:
    for movie_id in movie_ids:
        db.execute(user_favorite_movies.insert().values(user_id=user_id, movie_id=movie_id))


def insert_interactions(db: Session, user_id: int, spec: TestUserSpec) -> None:
    pinned_ids = select_movies_for_genres(db, spec.pinned_genre_names, limit=2, offset=6)
    watched_ids = select_movies_for_genres(db, spec.watched_genre_names, limit=2, offset=8)
    passed_ids = select_movies_for_genres(db, spec.passed_genre_names, limit=2, offset=10)
    for movie_id in sorted(set(pinned_ids + watched_ids + passed_ids)):
        db.add(
            UserInteraction(
                user_id=user_id,
                movie_id=movie_id,
                is_pinned=movie_id in pinned_ids,
                is_watched=movie_id in watched_ids,
                is_passed=movie_id in passed_ids,
            )
        )


def select_movies_for_genres(
    db: Session,
    genre_names: tuple[str, ...],
    *,
    limit: int,
    offset: int = 0,
) -> list[int]:
    if not genre_names:
        return []
    stmt = (
        select(Movie.id)
        .join(Movie.genres)
        .where(Genre.name.in_(genre_names))
        .where(Movie.adult.is_(False))
        .where(Movie.popularity.is_not(None))
        .group_by(Movie.id)
        .order_by(func.count(Genre.id).desc(), Movie.popularity.desc(), Movie.vote_average.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def run_worker() -> list[dict]:
    db = SessionLocal()
    try:
        return run_seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    seeded = run_worker()
    print(f"seeded v2 test users password={TEST_PASSWORD}")
    for row in seeded:
        print(row)
