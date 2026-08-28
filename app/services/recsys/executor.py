from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import ParamSpec, TypeVar

from app.core.config import settings


P = ParamSpec("P")
R = TypeVar("R")


class RecommendationExecutor:
    def __init__(self, max_workers: int) -> None:
        if max_workers <= 0:
            raise ValueError("recommendation executor workers must be positive")
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="recsys-request",
        )

    async def run(self, function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(function, *args, **kwargs),
        )

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


recommendation_executor = RecommendationExecutor(settings.RECOMMENDATION_EXECUTOR_WORKERS)
