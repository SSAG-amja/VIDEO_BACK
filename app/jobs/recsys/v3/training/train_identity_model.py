from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.db.session import SessionLocal
from app.jobs.recsys.v3.training.artifact_publisher import publish_identity_artifact
from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_identity_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the V3 identity-only LightFM baseline")
    parser.add_argument("--output-root", type=Path, default=Path("assets/ml_models/v3"))
    parser.add_argument("--data-cutoff-at", type=datetime.fromisoformat)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--num-threads", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides = {
        key: value
        for key, value in {
            "epochs": args.epochs,
            "num_threads": args.num_threads,
        }.items()
        if value is not None
    }
    config = LightFMTrainingConfig(**overrides)
    with SessionLocal() as db:
        dataset = build_lightfm_dataset(db, data_cutoff_at=args.data_cutoff_at)
    try:
        result = train_identity_model(dataset, config)
    except ValueError as exc:
        raise SystemExit(f"V3 identity-only training rejected: {exc}") from exc
    artifact_path = publish_identity_artifact(result, args.output_root)
    print(
        json.dumps(
            {
                "status": "ok",
                "model_build_id": result.model_build_id,
                "artifact_path": str(artifact_path),
                "diagnostics": result.diagnostics,
                "artifact_reload_exact_match": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
