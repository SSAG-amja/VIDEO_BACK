from functools import lru_cache
from importlib import import_module
from typing import cast

from app.core.config import settings
from app.services.recsys.contracts import RecommendationEngineAdapter


class UnsupportedRecommendationEngineError(ValueError):
    pass


def get_recommendation_adapter(engine_name: str | None = None) -> RecommendationEngineAdapter:
    selected = (engine_name or settings.RECOMMENDATION_ENGINE).strip().lower()
    if not selected.startswith("v") or not selected[1:].isdigit():
        raise UnsupportedRecommendationEngineError(
            f"unsupported recommendation engine={selected!r}; expected v<number>"
        )
    module_name = f"app.services.recsys.{selected}.adapter"
    try:
        return _load_adapter(module_name)
    except ModuleNotFoundError as exc:
        if exc.name in {module_name, module_name.rsplit(".", 1)[0]}:
            raise UnsupportedRecommendationEngineError(
                f"recommendation engine package not found: {selected!r}"
            ) from exc
        raise


@lru_cache(maxsize=None)
def _load_adapter(module_name: str) -> RecommendationEngineAdapter:
    adapter_class = getattr(import_module(module_name), "RecommendationAdapter")
    return cast(RecommendationEngineAdapter, adapter_class())
