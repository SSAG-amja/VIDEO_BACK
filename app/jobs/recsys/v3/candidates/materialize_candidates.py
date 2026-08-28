from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact
from app.jobs.recsys.v3.candidates.candidate_publisher import (
    publish_candidate_snapshot,
    validate_snapshot_publication_state,
)
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig
from app.jobs.recsys.v3.candidates.candidate_snapshot import materialize_candidate_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize exact V3 LightFM top-150 candidates (100 active + 50 reserve)"
    )
    parser.add_argument("model_artifact", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("assets/ml_models/v3/candidate_snapshots"),
    )
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--user-block-size", type=int, default=None)
    parser.add_argument("--item-block-size", type=int, default=None)
    parser.add_argument("--checkpoint-user-count", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--publish", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides = {
        key: value
        for key, value in {
            "top_k": args.top_k,
            "user_block_size": args.user_block_size,
            "item_block_size": args.item_block_size,
            "checkpoint_user_count": args.checkpoint_user_count,
            "worker_count": args.workers,
        }.items()
        if value is not None
    }
    config = CandidateMaterializationConfig(**overrides)
    artifact = load_hybrid_artifact(args.model_artifact)
    with SessionLocal() as db:
        eligible_user_ids, exclusions = load_eligible_users_and_exclusions(db, artifact.user_ids)
        db.rollback()
    snapshot = materialize_candidate_snapshot(
        artifact,
        exclusions_by_user_id=exclusions,
        eligible_user_ids=eligible_user_ids,
        config=config,
        output_root=args.output_root,
    )

    publication = None
    if args.publish:
        with SessionLocal() as db:
            try:
                validate_snapshot_publication_state(db, snapshot, artifact.user_ids)
                publication = publish_candidate_snapshot(db, snapshot)
                db.commit()
            except Exception:
                db.rollback()
                raise
    print(
        json.dumps(
            {
                "status": "ok",
                "candidate_snapshot_id": snapshot.snapshot_id,
                "snapshot_path": str(snapshot.path),
                "diagnostics": snapshot.manifest,
                "publication": publication,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
