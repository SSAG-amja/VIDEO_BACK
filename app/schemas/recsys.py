from enum import StrEnum

from pydantic import BaseModel


class RecommendationMode(StrEnum):
    ALL = "all"
    SUBSCRIBED_ONLY = "subscribed_only"


class InteractionAction(StrEnum):
    PASSED = "passed"
    PINNED = "pinned"
    WATCHED = "watched"
    SAVED = "saved"


class RecommendationResponse(BaseModel):
    user_id: int
    mode: RecommendationMode
    movie_ids: list[int]
    limit: int
    offset: int
    count: int
    has_more: bool
    source: str


class InteractionCreate(BaseModel):
    user_id: int
    movie_id: int
    action: InteractionAction


class ColdStartRequest(BaseModel):
    user_id: int
