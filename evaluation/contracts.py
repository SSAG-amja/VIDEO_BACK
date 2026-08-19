from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Interaction:
    movie_id: int
    rating: float
    timestamp: int


@dataclass(frozen=True, slots=True)
class RecommendationInput:
    user_id: int
    training_interactions: tuple[Interaction, ...]
    candidate_movie_ids: tuple[int, ...]


class EvaluationEngine(Protocol):
    """Contract implemented by every recommendation engine under evaluation."""

    name: str
    version: str

    def prepare(self, inputs: Sequence[RecommendationInput]) -> None: ...

    def rank_candidates(self, input_data: RecommendationInput) -> list[int]: ...

