from __future__ import annotations

import asyncio
import threading
import time
import unittest

from app.services.recsys.executor import RecommendationExecutor


class RecommendationExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_workers_process_queued_tasks_dynamically(self) -> None:
        executor = RecommendationExecutor(max_workers=2)
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        completed: list[int] = []

        def work(value: int) -> int:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02 if value % 2 else 0.04)
            with lock:
                completed.append(value)
                active -= 1
            return value * 10

        try:
            results = await asyncio.gather(*(executor.run(work, value) for value in range(6)))
        finally:
            executor.shutdown()

        self.assertEqual(results, [0, 10, 20, 30, 40, 50])
        self.assertEqual(sorted(completed), list(range(6)))
        self.assertEqual(maximum_active, 2)

    def test_rejects_non_positive_worker_count(self) -> None:
        with self.assertRaises(ValueError):
            RecommendationExecutor(max_workers=0)


if __name__ == "__main__":
    unittest.main()
