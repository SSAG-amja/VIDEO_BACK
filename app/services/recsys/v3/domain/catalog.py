from __future__ import annotations

from sqlalchemy import func

from app.models.movie import Movie


def eligible_catalog_movie_clause():
    """Return the shared V3 model, ontology, and serving catalog boundary."""
    display_title = func.coalesce(
        func.nullif(func.trim(Movie.title_ko), ""),
        func.nullif(func.trim(Movie.title), ""),
    )
    return Movie.adult.is_(False), display_title.is_not(None)
