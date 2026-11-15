from app.services.recsys.v2.schemas import CandidateScore


def build_explanation_tags(candidate: CandidateScore) -> list[str]:
    return candidate.explanation_tags
