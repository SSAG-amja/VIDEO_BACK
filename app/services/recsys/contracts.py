from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.schemas.recsys import RecommendationMode, RecommendationResponse


@dataclass(frozen=True, slots=True)
class RecommendationQuery:
    user_id: int
    mode: RecommendationMode
    limit: int
    offset: int = 0
    shuffle_seed: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationInteraction:
    movie_id: int
    rating: float
    timestamp: int


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    user_id: int
    training_interactions: tuple[EvaluationInteraction, ...]
    candidate_movie_ids: tuple[int, ...]


class EvaluationEngine(Protocol):
    name: str
    version: str

    def prepare(self, inputs: list[EvaluationInput]) -> None: ...

    def rank(self, input_data: EvaluationInput) -> list[int]: ...

    def close(self) -> None: ...


class RecommendationEngineAdapter(Protocol):
    name: str
    max_page_size: int

    def get_recommendations(self, db: Session, query: RecommendationQuery) -> RecommendationResponse: ...

    def refresh_cold_start(self, db: Session, user_id: int) -> None: ...

    def create_evaluation_engine(self) -> EvaluationEngine: ...
