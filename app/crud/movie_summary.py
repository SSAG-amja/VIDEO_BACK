from app.models import movie as movie_model
from app.schemas import playlist as playlist_schema


# 2026.05.13 박현식
# 공용 영화 요약 DTO를 만든다.
def movie_summary(movie: movie_model.Movie) -> playlist_schema.MovieSummary:
    return playlist_schema.MovieSummary(
        movie_id=movie.tmdb_id,
        movie_title=movie.title_ko or movie.title or movie.original_title,
        poster_path=movie.poster_path,
    )
