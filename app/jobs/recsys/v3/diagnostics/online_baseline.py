from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.db.session import SessionLocal
from app.models.mapping import MovieOtt, UserInteraction, user_otts
from app.models.ontology_recommendations import OntologyRecommendation
from app.models.recommendation_runs import RecommendationRun
from app.models.user import User
from app.schemas.recsys import RecommendationMode
from app.services.recsys.contracts import RecommendationQuery
from app.services.recsys.v3.adapter import V3RecommendationAdapter
from app.services.recsys.v3.serving.serving_bundle import get_active_serving_bundle


EMAIL_PATTERN = re.compile(r"^v3seed-(train|cold)-(\d{3})@pinlm\.test$")
REQUIRED_SCORE_TRACE_KEYS = {
    "model_raw_score",
    "normalized_long_term_score",
    "normalized_short_term_score",
    "ontology_raw_score",
    "normalized_ontology_score",
    "personal_component",
    "ontology_component",
    "base_score",
    "catalog_trust_penalty",
    "negative_preference_penalty",
    "repetition_penalty",
    "mmr_similarity_penalty",
    "final_score",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure the V3 seeded online baseline without accuracy metrics"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("z_v3_docs/diagnostics"),
    )
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit <= 0 or args.limit > 100:
        raise SystemExit("limit must be between 1 and 100")

    users = load_seed_users()
    training = [item for item in users if item["fixture_stage"] == "train"]
    cold = [item for item in users if item["fixture_stage"] == "cold"]
    if len(training) != 120 or len(cold) != 24:
        raise SystemExit(
            f"expected 120 training and 24 cold users; got {len(training)} and {len(cold)}"
        )

    adapter = V3RecommendationAdapter()
    exclusions = load_exclusions([item["user_id"] for item in users])
    started_at = datetime.now(timezone.utc)

    process_cold = run_case(
        adapter,
        training[0],
        mode=RecommendationMode.ALL,
        limit=args.limit,
        exclusions=exclusions,
        measurement="process_cold_load",
    )
    bundle = get_active_serving_bundle()
    known_warm = [
        run_case(
            adapter,
            user,
            mode=RecommendationMode.ALL,
            limit=args.limit,
            exclusions=exclusions,
            measurement="known_warm",
        )
        for user in training
    ]
    cold_warm = [
        run_case(
            adapter,
            user,
            mode=RecommendationMode.ALL,
            limit=args.limit,
            exclusions=exclusions,
            measurement="cold_warm",
        )
        for user in cold
    ]
    subscribed_only = [
        run_case(
            adapter,
            user,
            mode=RecommendationMode.SUBSCRIBED_ONLY,
            limit=args.limit,
            exclusions=exclusions,
            measurement="subscribed_only",
        )
        for user in training[:24]
    ]
    onboarding_mutation = [
        run_case(
            adapter,
            user,
            mode=RecommendationMode.ALL,
            limit=args.limit,
            exclusions=exclusions,
            measurement="onboarding_mutation",
        )
        for user in training[:6]
    ]
    page_checks = [
        run_page_check(adapter, user, exclusions=exclusions)
        for user in (training[0], cold[0])
    ]

    groups = {
        "process_cold_load": [process_cold],
        "known_warm": known_warm,
        "cold_warm": cold_warm,
        "subscribed_only": subscribed_only,
        "onboarding_mutation": onboarding_mutation,
    }
    failed_cases = [
        item
        for rows in groups.values()
        for item in rows
        if item["error"] is not None or item["violations"]
    ]
    failed_page_checks = [item for item in page_checks if not item["passed"]]
    report = {
        "status": "ok" if not failed_cases and not failed_page_checks else "failed",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "accuracy_metrics": [],
            "training_user_count": len(training),
            "cold_user_count": len(cold),
            "request_limit": args.limit,
        },
        "bundle": {
            "bundle_id": bundle.bundle_id,
            "model_build_id": bundle.model.model_build_id,
            "candidate_snapshot_id": bundle.candidate_snapshot_id,
            "ontology_build_id": bundle.ontology_build_id,
        },
        "summary": {name: summarize(rows) for name, rows in groups.items()},
        "source_counts": dict(
            sorted(
                Counter(
                    item["source"]
                    for rows in groups.values()
                    for item in rows
                    if item["source"] is not None
                ).items()
            )
        ),
        "candidate_path_counts": dict(
            sorted(
                Counter(
                    item["candidate_path"]
                    for rows in groups.values()
                    for item in rows
                    if item["candidate_path"] is not None
                ).items()
            )
        ),
        "short_term_cache_status_counts": dict(
            sorted(
                Counter(
                    item["short_term_cache_status"]
                    for rows in groups.values()
                    for item in rows
                    if item["short_term_cache_status"] is not None
                ).items()
            )
        ),
        "failed_case_count": len(failed_cases),
        "failed_page_check_count": len(failed_page_checks),
        "page_checks": page_checks,
        "measurements": groups,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_root / f"v3_online_baseline_{timestamp}.json"
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output_path": str(output_path),
                "summary": report["summary"],
                "source_counts": report["source_counts"],
                "candidate_path_counts": report["candidate_path_counts"],
                "short_term_cache_status_counts": report[
                    "short_term_cache_status_counts"
                ],
                "failed_case_count": len(failed_cases),
                "failed_page_check_count": len(failed_page_checks),
            },
            sort_keys=True,
        )
    )
    if report["status"] != "ok":
        raise SystemExit(1)


def load_seed_users() -> list[dict[str, object]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(User.id, User.email)
            .where(
                User.deleted_at.is_(None),
                or_(
                    User.email.like("v3seed-train-%@pinlm.test"),
                    User.email.like("v3seed-cold-%@pinlm.test"),
                ),
            )
            .order_by(User.email)
        ).all()
    users = []
    for user_id, email in rows:
        match = EMAIL_PATTERN.fullmatch(email)
        if match is None:
            raise ValueError(f"unexpected V3 seed email: {email}")
        stage, number_text = match.groups()
        number = int(number_text)
        users.append(
            {
                "user_id": int(user_id),
                "email": email,
                "fixture_stage": stage,
                "fixture_number": number,
                "profile_type": profile_type(stage, number),
            }
        )
    return users


def profile_type(stage: str, number: int) -> str:
    if stage == "train":
        if number <= 72:
            return "stable"
        if number <= 96:
            return "mixed"
        if number <= 108:
            return "drift"
        return "negative_heavy"
    if number <= 8:
        return "genre_favorite"
    if number <= 16:
        return "genre_only"
    if number <= 20:
        return "ott_only"
    return "empty_profile"


def load_exclusions(user_ids: list[int]) -> dict[int, frozenset[int]]:
    values: dict[int, set[int]] = {user_id: set() for user_id in user_ids}
    with SessionLocal() as db:
        rows = db.execute(
            select(UserInteraction.user_id, UserInteraction.movie_id).where(
                UserInteraction.user_id.in_(user_ids),
                or_(
                    UserInteraction.is_watched.is_(True),
                    UserInteraction.is_passed.is_(True),
                ),
            )
        ).all()
    for user_id, movie_id in rows:
        values[int(user_id)].add(int(movie_id))
    return {user_id: frozenset(movie_ids) for user_id, movie_ids in values.items()}


def run_case(
    adapter: V3RecommendationAdapter,
    user: dict[str, object],
    *,
    mode: RecommendationMode,
    limit: int,
    exclusions: dict[int, frozenset[int]],
    measurement: str,
    offset: int = 0,
) -> dict[str, object]:
    user_id = int(user["user_id"])
    request_marker = f"v3-baseline-{uuid.uuid4().hex}"
    started = time.perf_counter()
    try:
        with SessionLocal() as db:
            response = adapter.get_recommendations(
                db,
                RecommendationQuery(
                    user_id=user_id,
                    mode=mode,
                    limit=limit,
                    offset=offset,
                    shuffle_seed=request_marker,
                ),
            )
            elapsed_seconds = time.perf_counter() - started
            diagnostics = load_diagnostics(db, request_marker)
            subscribed_ids = (
                load_subscribed_streaming_movie_ids(db, user_id, response.movie_ids)
                if mode == RecommendationMode.SUBSCRIBED_ONLY
                else set(response.movie_ids)
            )
        violations = validate_response(
            response.movie_ids,
            response_count=response.count,
            limit=limit,
            excluded=exclusions[user_id],
            subscribed_ids=subscribed_ids,
            diagnostics=diagnostics,
            allow_empty_candidates=mode == RecommendationMode.SUBSCRIBED_ONLY,
        )
        return {
            **user,
            "measurement": measurement,
            "mode": mode.value,
            "offset": offset,
            "count": response.count,
            "has_more": response.has_more,
            "source": response.source,
            "movie_ids": response.movie_ids,
            "elapsed_seconds": round(elapsed_seconds, 6),
            "diagnostic_candidate_count": diagnostics["candidate_count"],
            "candidate_path": diagnostics["candidate_path"],
            "short_term_cache_status": diagnostics["short_term_cache_status"],
            "short_term_candidate_count": diagnostics["short_term_candidate_count"],
            "violations": violations,
            "error": None,
        }
    except Exception as exc:
        return {
            **user,
            "measurement": measurement,
            "mode": mode.value,
            "offset": offset,
            "count": 0,
            "has_more": False,
            "source": None,
            "movie_ids": [],
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "diagnostic_candidate_count": 0,
            "candidate_path": None,
            "short_term_cache_status": None,
            "short_term_candidate_count": 0,
            "violations": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def load_diagnostics(db, request_marker: str) -> dict[str, object]:
    rows = list(
        db.execute(
            select(OntologyRecommendation)
            .where(OntologyRecommendation.feed_session_key == request_marker)
            .order_by(OntologyRecommendation.candidate_stage, OntologyRecommendation.rank)
        ).scalars()
    )
    run = db.get(RecommendationRun, rows[0].run_id) if rows else db.scalar(
        select(RecommendationRun)
        .where(
            RecommendationRun.engine == "v3",
            RecommendationRun.run_type == "request",
            RecommendationRun.config_snapshot["request_marker"].as_string()
            == request_marker,
        )
        .order_by(RecommendationRun.started_at.desc())
        .limit(1)
    )
    if run is None:
        raise ValueError("request diagnostics were not persisted")
    if run.status != "success":
        raise ValueError("request diagnostic run did not finish successfully")
    request_path = (run.config_snapshot or {}).get("request_path") or {}
    candidate_rows = [row for row in rows if row.candidate_stage == "candidate_slice"]
    final_rows = [row for row in rows if row.candidate_stage == "final_response"]
    score_trace_complete = all(
        REQUIRED_SCORE_TRACE_KEYS
        <= set(((row.source_scores or {}).get("score_trace") or {}).keys())
        for row in rows
    )
    attribution_valid = all(
        not bool(reason.get("is_model_attribution"))
        for row in rows
        for reason in (row.explanation_tags or [])
    )
    return {
        "candidate_count": len(candidate_rows),
        "final_count": len(final_rows),
        "score_trace_complete": score_trace_complete,
        "attribution_valid": attribution_valid,
        "candidate_path": request_path.get("candidate_path"),
        "short_term_cache_status": request_path.get("short_term_cache_status"),
        "short_term_candidate_count": int(
            request_path.get("short_term_candidate_count") or 0
        ),
    }


def load_subscribed_streaming_movie_ids(db, user_id: int, movie_ids: list[int]) -> set[int]:
    if not movie_ids:
        return set()
    rows = db.execute(
        select(MovieOtt.movie_id)
        .join(user_otts, user_otts.c.ott_id == MovieOtt.ott_id)
        .where(
            user_otts.c.user_id == user_id,
            MovieOtt.movie_id.in_(movie_ids),
            MovieOtt.is_streaming.is_(True),
        )
        .distinct()
    ).scalars()
    return {int(movie_id) for movie_id in rows}


def validate_response(
    movie_ids: list[int],
    *,
    response_count: int,
    limit: int,
    excluded: frozenset[int],
    subscribed_ids: set[int],
    diagnostics: dict[str, object],
    allow_empty_candidates: bool = False,
) -> list[str]:
    violations = []
    if response_count != len(movie_ids):
        violations.append("response_count_mismatch")
    if len(movie_ids) > limit:
        violations.append("response_limit_exceeded")
    if len(movie_ids) != len(set(movie_ids)):
        violations.append("duplicate_movies")
    if excluded.intersection(movie_ids):
        violations.append("watched_or_passed_returned")
    if set(movie_ids) != subscribed_ids:
        violations.append("subscribed_only_availability_mismatch")
    if diagnostics["final_count"] != len(movie_ids):
        violations.append("final_diagnostic_count_mismatch")
    if diagnostics["candidate_count"] > 100:
        violations.append("candidate_pool_exceeded")
    if diagnostics["candidate_count"] == 0 and not allow_empty_candidates:
        violations.append("candidate_pool_empty")
    if diagnostics["candidate_path"] not in {"known_user_hybrid", "cold_start"}:
        violations.append("candidate_path_missing")
    if not diagnostics["score_trace_complete"]:
        violations.append("score_trace_incomplete")
    if not diagnostics["attribution_valid"]:
        violations.append("ontology_reason_marked_as_model_attribution")
    return violations


def run_page_check(
    adapter: V3RecommendationAdapter,
    user: dict[str, object],
    *,
    exclusions: dict[int, frozenset[int]],
) -> dict[str, object]:
    full = run_case(
        adapter,
        user,
        mode=RecommendationMode.ALL,
        limit=40,
        exclusions=exclusions,
        measurement="page_check_full",
    )
    first = run_case(
        adapter,
        user,
        mode=RecommendationMode.ALL,
        limit=20,
        exclusions=exclusions,
        measurement="page_check_first",
    )
    second = run_case(
        adapter,
        user,
        mode=RecommendationMode.ALL,
        limit=20,
        offset=20,
        exclusions=exclusions,
        measurement="page_check_second",
    )
    errors = [item["error"] for item in (full, first, second) if item["error"]]
    violations = [
        violation
        for item in (full, first, second)
        for violation in item["violations"]
    ]
    consistent = full["movie_ids"] == first["movie_ids"] + second["movie_ids"]
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "passed": not errors and not violations and consistent,
        "page_order_consistent": consistent,
        "errors": errors,
        "violations": violations,
    }


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    elapsed = sorted(float(item["elapsed_seconds"]) for item in rows)
    return {
        "request_count": len(rows),
        "success_count": sum(item["error"] is None for item in rows),
        "failure_count": sum(item["error"] is not None for item in rows),
        "invariant_failure_count": sum(bool(item["violations"]) for item in rows),
        "average_seconds": round(statistics.fmean(elapsed), 6),
        "median_seconds": round(statistics.median(elapsed), 6),
        "p95_seconds": round(percentile(elapsed, 0.95), 6),
        "max_seconds": round(max(elapsed), 6),
    }


def percentile(values: list[float], quantile: float) -> float:
    if not values or not 0 < quantile <= 1 or not math.isfinite(quantile):
        raise ValueError("percentile requires finite non-empty inputs")
    index = max(0, math.ceil(len(values) * quantile) - 1)
    return values[index]


if __name__ == "__main__":
    main()
