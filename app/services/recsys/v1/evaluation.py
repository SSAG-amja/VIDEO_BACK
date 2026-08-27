from __future__ import annotations

from app.db.session import SessionLocal
from app.jobs.recsys.v1.worker import (
    InteractionSignal,
    build_preference_profile,
    cosine_similarity,
    score_content_movie_ids,
)
from app.services.recsys.contracts import EvaluationInput


POSITIVE_RATING_MIN = 3.5


class V1EvaluationEngine:
    name = "v1"
    version = "v1-fixed-cohort"

    def __init__(self) -> None:
        self._inputs: dict[int, EvaluationInput] = {}
        self._vectors: dict[int, dict[int, float]] = {}

    def prepare(self, inputs: list[EvaluationInput]) -> None:
        self._inputs = {item.user_id: item for item in inputs}
        self._vectors = {
            item.user_id: {row.movie_id: row.rating - 3.0 for row in item.training_interactions}
            for item in inputs
        }

    def rank(self, input_data: EvaluationInput) -> list[int]:
        db = SessionLocal()
        try:
            signals = [
                InteractionSignal(
                    movie_id=row.movie_id,
                    score=max(row.rating - 2.5, 0.0) if row.rating >= POSITIVE_RATING_MIN else 0.0,
                    exclude_from_feed=True,
                )
                for row in input_data.training_interactions
            ]
            profile = build_preference_profile(db, signals)
            candidate_ids = list(input_data.candidate_movie_ids)
            scores = score_content_movie_ids(db, profile, candidate_ids)
            target = self._vectors.get(input_data.user_id, {})
            candidate_set = set(candidate_ids)
            for other_user_id, other in self._vectors.items():
                if other_user_id == input_data.user_id:
                    continue
                similarity = cosine_similarity(target, other)
                if similarity <= 0:
                    continue
                for row in self._inputs[other_user_id].training_interactions:
                    if row.movie_id in candidate_set and row.rating >= POSITIVE_RATING_MIN:
                        scores[row.movie_id] = scores.get(row.movie_id, 0.0) + similarity * row.rating
            return sorted(candidate_ids, key=lambda movie_id: (-scores.get(movie_id, 0.0), movie_id))
        finally:
            db.close()

    def metadata(self) -> dict:
        return {"candidate_scope": "all_holdout", "owner": "app.services.recsys.v1"}

    def close(self) -> None:
        return None
