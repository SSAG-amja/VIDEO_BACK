from __future__ import annotations

from app.crud.recsys.ontology import get_active_build
from app.db.session import SessionLocal
from app.services.recsys.contracts import EvaluationInput
from app.services.recsys.v2.candidate_generator import generate_candidates_for_movie_ids
from app.services.recsys.v2.profile_builder import build_evaluation_profile
from app.services.recsys.v2.ranker import rank_candidates
from app.services.recsys.v2.schemas import CandidateScore, SessionProfile
from app.services.recsys.v2.scorer import score_candidates


class V2EvaluationEngine:
    name = "v2"
    version = "v2-fixed-cohort"

    def __init__(self) -> None:
        self._build_id: int | None = None

    def prepare(self, inputs: list[EvaluationInput]) -> None:
        del inputs
        db = SessionLocal()
        try:
            build = get_active_build(db)
            if build is None:
                raise RuntimeError("V2 evaluation requires an active ontology build")
            self._build_id = int(build.id)
        finally:
            db.close()

    def rank(self, input_data: EvaluationInput) -> list[int]:
        db = SessionLocal()
        try:
            profile = build_evaluation_profile(
                db,
                user_id=input_data.user_id,
                **_movie_sets(input_data),
            )
            candidates, filtered_movie_ids = generate_candidates_for_movie_ids(
                db,
                profile=profile,
                movie_ids=list(input_data.candidate_movie_ids),
            )
            session = SessionProfile(feed_session_key="fixed_cohort_evaluation")
            ranked = rank_candidates(
                score_candidates(candidates, profile=profile, session_profile=session)
            )
            filtered_tail = [
                CandidateScore(movie_id=movie_id, score=0.0, source="quality_filtered_tail")
                for movie_id in filtered_movie_ids
            ]
            return [int(candidate.movie_id) for candidate in [*ranked, *filtered_tail]]
        finally:
            db.close()

    def metadata(self) -> dict:
        return {
            "candidate_scope": "all_holdout",
            "owner": "app.services.recsys.v2",
            "ontology_build_id": self._build_id,
        }

    def close(self) -> None:
        return None


def _movie_sets(input_data: EvaluationInput) -> dict[str, set[int]]:
    favorite: list[int] = []
    pinned: list[int] = []
    passed: list[int] = []
    for interaction in input_data.training_interactions:
        rating = float(interaction.rating)
        if rating == 5.0:
            favorite.append(int(interaction.movie_id))
        elif rating >= 3.5:
            pinned.append(int(interaction.movie_id))
        elif rating <= 1.5:
            passed.append(int(interaction.movie_id))
    return {
        "favorite_movie_ids": set(favorite),
        "pinned_movie_ids": set(pinned),
        "passed_movie_ids": set(passed),
        "watched_movie_ids": set(),
    }
