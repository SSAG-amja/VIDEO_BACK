from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.db.session import SessionLocal
from app.jobs.recsys.v3.training.artifact_publisher import publish_hybrid_artifact
from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.features.feature_builder import export_item_features
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_hybrid_model, validate_identity_dataset
from app.jobs.recsys.v3.features.user_feature_builder import export_user_features


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the V3 ontology hybrid LightFM model")
    parser.add_argument("ontology_build_id", type=int)
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
    config = LightFMTrainingConfig(stage="hybrid_ontology", **overrides)
    with SessionLocal() as db:
        dataset = build_lightfm_dataset(db, data_cutoff_at=args.data_cutoff_at)
        try:
            validate_identity_dataset(dataset)
        except ValueError as exc:
            raise SystemExit(f"V3 hybrid training rejected before feature export: {exc}") from exc
        item_export = export_item_features(db, args.ontology_build_id)
        user_export = export_user_features(
            db,
            user_ids=dataset.user_ids,
            item_export=item_export,
        )
        result = train_hybrid_model(
            dataset,
            item_export=item_export,
            user_export=user_export,
            config=config,
        )
        db.rollback()
    artifact_path = publish_hybrid_artifact(result, args.output_root)
    print(
        json.dumps(
            {
                "status": "ok",
                "model_build_id": result.model_build_id,
                "artifact_path": str(artifact_path),
                "ontology_build_id": item_export.manifest.ontology_build_id,
                "diagnostics": result.diagnostics,
                "artifact_reload_exact_match": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
