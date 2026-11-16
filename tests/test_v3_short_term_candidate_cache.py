from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from app.services.recsys.profile_change import (
    PROFILE_REFRESH_LEASE_SECONDS,
    V3_SHORT_TERM_PROCESSING_USERS_KEY,
    V3_SHORT_TERM_SCHEDULED_USERS_KEY,
    claim_recommendation_profile_refreshes,
    complete_recommendation_profile_refresh,
    mark_recommendation_profile_changed,
    profile_version_key,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    ShortTermCandidate,
    ShortTermRetrievalDiagnostics,
    ShortTermRetrievalResult,
)
from app.services.recsys.v3.config import (
    SHORT_TERM_CANDIDATE_CACHE_TTL_JITTER_SECONDS,
    SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS,
)
from app.services.recsys.v3.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
    short_term_candidate_cache_key,
    short_term_profile_signature,
)
from tests.test_v3_retrieval import retrieval_profile


class _Pipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple]] = []

    def incr(self, key: str):
        self.operations.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int):
        self.operations.append(("expire", (key, seconds)))
        return self

    def sadd(self, key: str, value: int):
        self.operations.append(("sadd", (key, value)))
        return self

    def execute(self):
        for name, arguments in self.operations:
            getattr(self.redis, name)(*arguments)
        return [True] * len(self.operations)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True) -> _Pipeline:
        return _Pipeline(self)

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    def incr(self, key: str):
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    def expire(self, key: str, seconds: int):
        return key in self.values

    def sadd(self, key: str, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(str(value) for value in values)
        return len(target) - before

    def eval(self, _script: str, key_count: int, *arguments):
        scheduled_key, processing_key, now, count, lease_until = arguments
        processing = self.sorted_sets.setdefault(processing_key, {})
        expired = [member for member, score in processing.items() if score <= float(now)]
        for member in expired:
            processing.pop(member)
            self.sorted_sets.setdefault(scheduled_key, {})[member] = float(now)
        scheduled = self.sorted_sets.setdefault(scheduled_key, {})
        selected = sorted(
            member for member, score in scheduled.items() if score <= float(now)
        )[: int(count)]
        for member in selected:
            scheduled.pop(member)
        processing.update({member: float(lease_until) for member in selected})
        return selected

    def zrem(self, key: str, *values):
        target = self.sorted_sets.setdefault(key, {})
        before = len(target)
        for value in values:
            target.pop(str(value), None)
        return before - len(target)


def _computed_result() -> ShortTermRetrievalResult:
    candidates = (
        ShortTermCandidate(movie_id=100, short_term_raw_score=2.0, source_rank=1),
        ShortTermCandidate(movie_id=200, short_term_raw_score=1.0, source_rank=2),
    )
    return ShortTermRetrievalResult(
        candidates=candidates,
        diagnostics=ShortTermRetrievalDiagnostics(
            ontology_build_id=22,
            profile_feature_count=2,
            excluded_movie_count=1,
            candidate_count=2,
            elapsed_seconds=1.0,
            query_count=2,
        ),
    )


class ShortTermCandidateCacheTest(unittest.TestCase):
    def test_first_request_materializes_and_second_request_hits_cache(self) -> None:
        redis = _FakeRedis()
        profile = retrieval_profile()
        with patch(
            "app.services.recsys.v3.retrieval.short_term_candidate_cache.retrieve_short_term_candidates",
            side_effect=lambda *_args, **kwargs: replace(
                _computed_result(),
                candidates=_computed_result().candidates[: kwargs["limit"]],
            ),
        ) as retrieve:
            first = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )
            second = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )

        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(first.diagnostics.cache_status, "miss_stored")
        self.assertEqual(second.diagnostics.cache_status, "hit")
        self.assertEqual(second.diagnostics.query_count, 0)
        self.assertEqual(second.candidates, first.candidates)
        ttl = redis.expirations[short_term_candidate_cache_key(profile.user_id)]
        self.assertGreaterEqual(ttl, SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS)
        self.assertLessEqual(
            ttl,
            SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS
            + SHORT_TERM_CANDIDATE_CACHE_TTL_JITTER_SECONDS,
        )
        self.assertEqual(
            ttl,
            SHORT_TERM_CANDIDATE_CACHE_TTL_SECONDS
            + profile.user_id % (SHORT_TERM_CANDIDATE_CACHE_TTL_JITTER_SECONDS + 1),
        )

    def test_single_live_profile_change_does_not_regenerate_short_term_candidates(self) -> None:
        redis = _FakeRedis()
        profile = retrieval_profile()
        with patch(
            "app.services.recsys.v3.retrieval.short_term_candidate_cache.retrieve_short_term_candidates",
            return_value=_computed_result(),
        ) as retrieve:
            before = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )
            self.assertTrue(mark_recommendation_profile_changed(redis, profile.user_id))
            after = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )

        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(
            before.diagnostics.profile_signature,
            after.diagnostics.profile_signature,
        )
        self.assertEqual(after.diagnostics.cache_status, "hit")
        self.assertEqual(redis.values[profile_version_key(profile.user_id)], "1")

    def test_invalid_cache_falls_back_to_retrieval_and_repairs_value(self) -> None:
        redis = _FakeRedis()
        profile = retrieval_profile()
        redis.values[short_term_candidate_cache_key(profile.user_id)] = "not-json"
        with patch(
            "app.services.recsys.v3.retrieval.short_term_candidate_cache.retrieve_short_term_candidates",
            return_value=_computed_result(),
        ) as retrieve:
            result = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )

        retrieve.assert_called_once()
        self.assertEqual(result.diagnostics.cache_status, "miss_stored")
        self.assertNotEqual(redis.values[short_term_candidate_cache_key(profile.user_id)], "not-json")

    def test_smaller_cached_limit_is_not_reused_for_a_larger_request(self) -> None:
        redis = _FakeRedis()
        profile = retrieval_profile()
        with patch(
            "app.services.recsys.v3.retrieval.short_term_candidate_cache.retrieve_short_term_candidates",
            side_effect=lambda *_args, **kwargs: replace(
                _computed_result(),
                candidates=_computed_result().candidates[: kwargs["limit"]],
            ),
        ) as retrieve:
            retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile, limit=1
            )
            retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile, limit=100
            )

        self.assertEqual(retrieve.call_count, 2)

    def test_signature_ignores_score_decay_and_time_but_tracks_ontology_build(self) -> None:
        profile = retrieval_profile()
        decayed_features = tuple(
            replace(
                signal,
                score=signal.score * 0.9,
                raw_score=(signal.raw_score * 0.9 if signal.raw_score is not None else None),
            )
            for signal in profile.short_term.positive_features
        )
        decayed = replace(
            profile,
            short_term=replace(profile.short_term, positive_features=decayed_features),
        )
        much_later = replace(
            decayed,
            short_term=replace(
                decayed.short_term,
                as_of=decayed.short_term.as_of + timedelta(days=1),
            ),
        )
        first = short_term_profile_signature(profile, ontology_build_id=22)
        decayed_later = short_term_profile_signature(much_later, ontology_build_id=22)
        next_build = short_term_profile_signature(much_later, ontology_build_id=23)

        self.assertEqual(first, decayed_later)
        self.assertNotEqual(first, next_build)

    def test_scheduled_users_are_claimed_once(self) -> None:
        redis = _FakeRedis()
        redis.sorted_sets[V3_SHORT_TERM_SCHEDULED_USERS_KEY] = {"7": 0.0, "9": 0.0}

        self.assertEqual(claim_recommendation_profile_refreshes(redis, 10), (7, 9))
        self.assertEqual(
            set(redis.sorted_sets[V3_SHORT_TERM_PROCESSING_USERS_KEY]),
            {"7", "9"},
        )
        self.assertTrue(complete_recommendation_profile_refresh(redis, 7))
        self.assertNotIn("7", redis.sorted_sets[V3_SHORT_TERM_PROCESSING_USERS_KEY])
        self.assertEqual(claim_recommendation_profile_refreshes(redis, 10), ())

    def test_expired_processing_lease_is_reclaimed(self) -> None:
        redis = _FakeRedis()
        redis.sorted_sets[V3_SHORT_TERM_PROCESSING_USERS_KEY] = {"11": 0.0}

        self.assertEqual(claim_recommendation_profile_refreshes(redis, 1), (11,))
        lease = redis.sorted_sets[V3_SHORT_TERM_PROCESSING_USERS_KEY]["11"]
        self.assertGreater(lease, PROFILE_REFRESH_LEASE_SECONDS)

    def test_cached_excluded_movie_is_filtered_without_regeneration(self) -> None:
        redis = _FakeRedis()
        profile = retrieval_profile()
        with patch(
            "app.services.recsys.v3.retrieval.short_term_candidate_cache.retrieve_short_term_candidates",
            return_value=_computed_result(),
        ) as retrieve:
            retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=profile
            )
            excluded = replace(
                profile,
                long_term=replace(
                    profile.long_term,
                    excluded_movie_ids=profile.long_term.excluded_movie_ids | {100},
                ),
            )
            result = retrieve_cached_short_term_candidates(
                object(), redis=redis, ontology_build_id=22, profile=excluded
            )

        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(result.diagnostics.cache_status, "hit_filtered")
        self.assertEqual([item.movie_id for item in result.candidates], [200])
        self.assertEqual(result.candidates[0].source_rank, 1)


if __name__ == "__main__":
    unittest.main()
