from app.services.recsys.contracts import EvaluationEngine
from app.services.recsys.registry import get_recommendation_adapter


def get_evaluation_engine(engine_name: str | None = None) -> EvaluationEngine:
    """Create the selected production engine's standard offline evaluator."""
    return get_recommendation_adapter(engine_name).create_evaluation_engine()
