from __future__ import annotations

import json
import time

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.recommendations import Recommendation
from app.models.user import User
from app.services.recsys.registry import get_recommendation_adapter


def target_users(db) -> tuple[tuple[int, str, bool], ...]:
    cold_rows = db.execute(
        select(User.id, User.email)
        .where(User.email.like("v3seed-cold-%@pinlm.test"))
        .order_by(User.email)
    ).all()
    training_rows = db.execute(
        select(User.id, User.email)
        .where(User.email.in_([f"v3seed-train-{number:03d}@pinlm.test" for number in range(1, 7)]))
        .order_by(User.email)
    ).all()
    if len(cold_rows) != 24 or len(training_rows) != 6:
        raise RuntimeError(
            f"expected 24 cold and 6 mutated training users; found {len(cold_rows)} and {len(training_rows)}"
        )

    targets: list[tuple[int, str, bool]] = []
    for user_id, email in cold_rows:
        cold_number = int(email.split("-")[-1].split("@")[0])
        if cold_number <= 16:
            targets.append((int(user_id), email, True))
        elif cold_number <= 20:
            targets.append((int(user_id), email, False))
    targets.extend((int(user_id), email, True) for user_id, email in training_rows)
    return tuple(targets)


def candidate_count(db, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Recommendation)
            .where(
                Recommendation.user_id == user_id,
                Recommendation.source == "lightfm_v3_feature_only",
            )
        )
        or 0
    )


def main() -> None:
    adapter = get_recommendation_adapter("v3")
    results: list[dict] = []
    with SessionLocal() as db:
        targets = target_users(db)
        for user_id, email, expect_candidates in targets:
            started = time.monotonic()
            adapter.refresh_cold_start(db, user_id)
            elapsed_seconds = time.monotonic() - started
            count = candidate_count(db, user_id)
            if expect_candidates and count == 0:
                raise RuntimeError(f"feature-only candidates are missing user_id={user_id}")
            if not expect_candidates and count != 0:
                raise RuntimeError(f"OTT-only user unexpectedly has model candidates user_id={user_id}")
            results.append(
                {
                    "user_id": user_id,
                    "email": email,
                    "expected_candidates": expect_candidates,
                    "candidate_count": count,
                    "elapsed_seconds": round(elapsed_seconds, 6),
                }
            )

    elapsed_values = [row["elapsed_seconds"] for row in results]
    print(
        json.dumps(
            {
                "status": "ok",
                "processed_user_count": len(results),
                "feature_only_user_count": sum(row["candidate_count"] > 0 for row in results),
                "average_elapsed_seconds": round(sum(elapsed_values) / len(elapsed_values), 6),
                "max_elapsed_seconds": max(elapsed_values),
                "users": results,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
