from functools import lru_cache
from importlib import import_module
from typing import cast

from app.core.config import settings
from app.services.recsys.contracts import RecommendationEngineAdapter


class UnsupportedRecommendationEngineError(ValueError):
    pass


_ADAPTER_PATHS = {
    "v1": "app.services.recsys.v1.adapter:V1RecommendationAdapter",
    "v2": "app.services.recsys.v2.adapter:V2RecommendationAdapter",
    "v3": "app.services.recsys.v3.adapter:V3RecommendationAdapter",
}


def get_recommendation_adapter(engine_name: str | None = None) -> RecommendationEngineAdapter:
    selected = (engine_name or settings.RECOMMENDATION_ENGINE).strip().lower()
    try:
        adapter_path = _ADAPTER_PATHS[selected]
    except KeyError as exc:
        supported = ", ".join(sorted(_ADAPTER_PATHS))
        raise UnsupportedRecommendationEngineError(
            f"unsupported recommendation engine={selected!r}; expected one of: {supported}"
        ) from exc
    return _load_adapter(adapter_path)


@lru_cache(maxsize=None)
def _load_adapter(adapter_path: str) -> RecommendationEngineAdapter:
    module_name, class_name = adapter_path.split(":", maxsplit=1)
    adapter_class = getattr(import_module(module_name), class_name)
    return cast(RecommendationEngineAdapter, adapter_class())
