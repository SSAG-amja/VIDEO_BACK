from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable

from sqlalchemy import event, select

from app.db.session import SessionLocal, engine
from app.models.user import User
from app.schemas.recsys import RecommendationMode
from app.services.recsys.v3 import recommender


REPRESENTATIVE_EMAILS = (
    "v3seed-train-007@pinlm.test",
    "v3seed-train-073@pinlm.test",
    "v3seed-train-097@pinlm.test",
    "v3seed-train-109@pinlm.test",
    "v3seed-train-001@pinlm.test",
    "v3seed-cold-001@pinlm.test",
    "v3seed-cold-009@pinlm.test",
    "v3seed-cold-017@pinlm.test",
    "v3seed-cold-021@pinlm.test",
)
OUTPUT_ROOT = Path("z_v3_docs/diagnostics")


def main() -> None:
    current: dict[str, float] = {}
    current_queries: list[dict[str, object]] = []
    current_email: str | None = None

    def before_cursor_execute(_conn, _cursor, _statement, _parameters, context, _many):
        if current_email is not None:
            context._v3_profile_started = time.perf_counter()

    def after_cursor_execute(_conn, _cursor, statement, _parameters, context, _many):
        started = getattr(context, "_v3_profile_started", None)
        if current_email is None or started is None:
            return
        current_queries.append(
            {
                "query": classify_query(statement),
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        )

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)

    def install(
        name: str,
        label: str,
        detail: Callable[[object], dict[str, float]] | None = None,
    ) -> None:
        original = getattr(recommender, name)

        @wraps(original)
        def timed(*args, **kwargs):
            started = time.perf_counter()
            result = original(*args, **kwargs)
            current[label] = current.get(label, 0.0) + time.perf_counter() - started
            if detail is not None:
                current.update(detail(result))
            return result

        setattr(recommender, name, timed)

    install("get_active_serving_bundle", "bundle_load")
    install("build_user_runtime_profile", "profile")
    install("_load_published_candidates", "published_candidate_load")
    install("onboarding_features_changed", "onboarding_change_check")
    install("retrieve_lightfm_candidates", "online_lightfm")
    install(
        "build_retrieval_candidates",
        "retrieval_pipeline",
        lambda result: {
            "short_term_retrieval": result.short_term.diagnostics.elapsed_seconds,
            "ontology_analysis": result.ontology.diagnostics.elapsed_seconds,
        },
    )
    install(
        "run_cold_start_pipeline",
        "cold_start_pipeline",
        lambda result: {
            "cold_rule_retrieval": result.retrieval.diagnostics.elapsed_seconds,
            "ontology_analysis": result.ontology.diagnostics.elapsed_seconds,
        },
    )
    install("evaluate_policy_candidates", "policy")
    install("evaluate_candidate_set", "policy")
    install("get_blacklisted_movie_ids", "redis_blacklist")
    install("_persist_request_diagnostics", "diagnostic_persist")

    users = load_users()
    rows = []
    for email in REPRESENTATIVE_EMAILS:
        current.clear()
        current_queries.clear()
        current_email = email
        user_id = users[email]
        started = time.perf_counter()
        error = None
        response = None
        try:
            with SessionLocal() as db:
                response = recommender.get_recommendations(
                    db,
                    user_id=user_id,
                    mode=RecommendationMode.ALL,
                    limit=20,
                    shuffle_seed=f"v3-stage-profile-{uuid.uuid4().hex}",
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        total = time.perf_counter() - started
        top_level = sum(
            current.get(key, 0.0)
            for key in (
                "bundle_load",
                "profile",
                "published_candidate_load",
                "onboarding_change_check",
                "online_lightfm",
                "retrieval_pipeline",
                "cold_start_pipeline",
                "policy",
                "redis_blacklist",
                "diagnostic_persist",
            )
        )
        stages = {key: round(value, 6) for key, value in sorted(current.items())}
        stages["unattributed"] = round(max(0.0, total - top_level), 6)
        rows.append(
            {
                "email": email,
                "user_id": user_id,
                "source": response.source if response is not None else None,
                "count": response.count if response is not None else 0,
                "total_seconds": round(total, 6),
                "stages": stages,
                "queries": sorted(
                    current_queries,
                    key=lambda item: float(item["elapsed_seconds"]),
                    reverse=True,
                ),
                "error": error,
            }
        )
        current_email = None

    report = {
        "status": "ok" if all(row["error"] is None for row in rows) else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_ROOT / "v3_online_stage_profile.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_path": str(output_path), **report}, sort_keys=True))
    if report["status"] != "ok":
        raise SystemExit(1)


def load_users() -> dict[str, int]:
    with SessionLocal() as db:
        rows = db.execute(
            select(User.email, User.id).where(User.email.in_(REPRESENTATIVE_EMAILS))
        ).all()
    users = {str(email): int(user_id) for email, user_id in rows}
    missing = set(REPRESENTATIVE_EMAILS) - users.keys()
    if missing:
        raise ValueError(f"missing representative V3 seed users: {sorted(missing)}")
    return users


def classify_query(statement: str) -> str:
    normalized = " ".join(statement.lower().split())
    if "short_term_raw_score" in normalized:
        return "short_term_reverse_lookup"
    if "profile_scope" in normalized and "matched_type" not in normalized:
        return "candidate_profile_aggregate"
    if "repetition_relation" in normalized:
        return "candidate_repetition_features"
    if "count(*) over" in normalized and "family_size" in normalized:
        return "profile_graph_edges"
    if normalized.startswith("insert into ontology_recommendations"):
        return "diagnostic_rows_insert"
    if normalized.startswith("insert into recommendation_runs"):
        return "diagnostic_run_insert"
    if normalized.startswith("update recommendation_runs"):
        return "diagnostic_run_update"
    if "from movie_otts" in normalized:
        return "candidate_ott_lookup"
    if "from recommendations" in normalized:
        return "published_candidate_lookup"
    if "from user_interactions" in normalized:
        return "profile_interactions"
    if "from playlist_movies" in normalized:
        return "profile_saved"
    if "from user_favorite_movies" in normalized:
        return "profile_favorites"
    if "from movies" in normalized:
        return "movie_metadata_or_fallback"
    return normalized[:100]


if __name__ == "__main__":
    main()
