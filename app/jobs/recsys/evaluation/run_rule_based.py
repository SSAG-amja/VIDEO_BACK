
"""seed_real_db.py로 시딩된 유저 전원에 대해 실제 rec_pipeline 로직(rule_based_eval 경유)을
실행해 claude/results/rule_based_candidates.json을 만든다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.jobs.recsys import rec_pipeline as rp
from app.jobs.recsys.evaluation.rule_based_eval import generate_user_candidates_restricted

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = PROJECT_ROOT / "claude" / "data"
RESULTS_DIR = PROJECT_ROOT / "claude" / "results"

TOP_K_SAVE = 50


def run() -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((DATA_DIR / "seed_manifest.json").read_text())

    db_user_id_by_ml_user_id: dict[str, int] = manifest["db_user_id_by_ml_user_id"]
    movie_id_map_ml_to_db: dict[str, int] = manifest["movie_id_map_ml_to_db"]
    db_to_ml_movie_id = {db_id: int(ml_id) for ml_id, db_id in movie_id_map_ml_to_db.items()}
    matched_movie_ids = set(db_to_ml_movie_id.keys())

    engine = create_engine(settings.DATABASE_URL)
    session_factory = sessionmaker(bind=engine)

    results: dict[str, list[list]] = {}
    started = time.time()

    with session_factory() as db:
        batch_context = rp.build_batch_context(db)
        print(f"batch_context users={len(batch_context.user_ids)} vectors={len(batch_context.user_vectors)}")

        for i, (ml_user_id, db_user_id) in enumerate(db_user_id_by_ml_user_id.items(), start=1):
            candidates = generate_user_candidates_restricted(
                db,
                db_user_id,
                settings.RECOMMENDATION_POOL_SIZE,
                matched_movie_ids,
                batch_context=batch_context,
            )
            top = candidates[:TOP_K_SAVE]
            results[ml_user_id] = [
                [db_to_ml_movie_id[c.movie_id], c.score, c.source] for c in top if c.movie_id in db_to_ml_movie_id
            ]
            if i % 50 == 0:
                elapsed = time.time() - started
                print(f"[{i}/{len(db_user_id_by_ml_user_id)}] elapsed={elapsed:.1f}s")

    (RESULTS_DIR / "rule_based_candidates.json").write_text(json.dumps(results, ensure_ascii=False))
    elapsed = time.time() - started
    empty_users = sum(1 for v in results.values() if not v)
    summary = {
        "user_count": len(results),
        "empty_result_users": empty_users,
        "elapsed_seconds": round(elapsed, 1),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run()
