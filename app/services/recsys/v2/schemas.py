from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class UserProfile:
    user_id: int
    profile_type: str
    favorite_movie_ids: set[int] = field(default_factory=set)
    saved_movie_ids: set[int] = field(default_factory=set)
    subscribed_ott_ids: set[int] = field(default_factory=set)
    genre_scores: dict[int, float] = field(default_factory=dict)
    keyword_scores: dict[int, float] = field(default_factory=dict)
    actor_scores: dict[int, float] = field(default_factory=dict)
    director_scores: dict[int, float] = field(default_factory=dict)
    theme_scores: dict[str, float] = field(default_factory=dict)
    mood_scores: dict[str, float] = field(default_factory=dict)
    negative_movie_ids: set[int] = field(default_factory=set)
    excluded_movie_ids: set[int] = field(default_factory=set)


@dataclass(slots=True)
class SessionProfile:
    feed_session_key: str
    refresh_count: int = 0
    recently_exposed_movie_ids: set[int] = field(default_factory=set)
    recent_skipped_movie_ids: set[int] = field(default_factory=set)
    recent_dwell_map: dict[int, int] = field(default_factory=dict)
    session_positive_concept_scores: dict[str, float] = field(default_factory=dict)
    session_negative_concept_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateScore:
    movie_id: int
    score: float
    source: str
    source_scores: dict[str, float] = field(default_factory=dict)
    explanation_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecommendationRequestContext:
    user_id: int
    request_id: str
    feed_session_key: str
    refresh_count: int
    page_size: int
    offset: int = 0
    subscribed_only: bool = False
