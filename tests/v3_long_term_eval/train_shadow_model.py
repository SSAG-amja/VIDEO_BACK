from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.db.session import SessionLocal
from app.jobs.recsys.v3.datasets.dataset_builder import build_lightfm_dataset
from app.jobs.recsys.v3.features.feature_builder import export_item_features
from app.jobs.recsys.v3.features.feature_representation import (
    transform_item_feature_export,
    transform_user_feature_export,
)
from app.jobs.recsys.v3.features.user_feature_builder import export_user_features
from app.jobs.recsys.v3.training.artifact_publisher import publish_hybrid_artifact
from app.jobs.recsys.v3.training.model_schemas import LightFMTrainingConfig
from app.jobs.recsys.v3.training.trainer import train_hybrid_model, validate_identity_dataset
from app.services.recsys.v3.config import (
    LIGHTFM_HYBRID_EPOCHS,
    LIGHTFM_HYBRID_FEATURE_REPRESENTATION,
    LIGHTFM_HYBRID_ITEM_ALPHA,
    LIGHTFM_HYBRID_ITEM_FIELD_BUDGETS,
    LIGHTFM_HYBRID_ITEM_IDENTITY_WEIGHT,
    LIGHTFM_HYBRID_ITEM_KEYWORD_WEIGHTING,
    LIGHTFM_HYBRID_ITEM_SEMANTIC_WEIGHT,
    LIGHTFM_HYBRID_KNOWN_USER_SCORE_CENTERING_WEIGHT,
    LIGHTFM_HYBRID_LEARNING_RATE,
    LIGHTFM_HYBRID_MAX_SAMPLED,
    LIGHTFM_HYBRID_NO_COMPONENTS,
    LIGHTFM_HYBRID_NUM_THREADS,
    LIGHTFM_HYBRID_USER_ALPHA,
    LIGHTFM_HYBRID_USER_IDENTITY_WEIGHT,
    LIGHTFM_HYBRID_USER_SEMANTIC_WEIGHT,
    TRAINING_ITEM_FREQUENCY_WEIGHTING,
    TRAINING_USER_ACTIVITY_WEIGHTING,
)
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.evaluation_dataset import (
    evaluation_user_ids,
    restrict_to_evaluation_users,
)


OUTPUT_ROOT = Path("tests/v3_long_term_eval/generated/models")
PLAN_PATH = Path("tests/v3_long_term_eval/generated/evaluation_plan.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train an inactive V3 shadow model for long-term evaluation"
    )
    parser.add_argument("ontology_build_id", type=int)
    parser.add_argument("--data-cutoff-at", type=datetime.fromisoformat)
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--epochs", type=int, default=LIGHTFM_HYBRID_EPOCHS)
    parser.add_argument("--num-threads", type=int, default=LIGHTFM_HYBRID_NUM_THREADS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plans = load_plan(args.plan)
    config = LightFMTrainingConfig(
        stage="hybrid_ontology",
        no_components=LIGHTFM_HYBRID_NO_COMPONENTS,
        epochs=args.epochs,
        learning_rate=LIGHTFM_HYBRID_LEARNING_RATE,
        user_alpha=LIGHTFM_HYBRID_USER_ALPHA,
        item_alpha=LIGHTFM_HYBRID_ITEM_ALPHA,
        max_sampled=LIGHTFM_HYBRID_MAX_SAMPLED,
        num_threads=args.num_threads,
        item_frequency_weighting=TRAINING_ITEM_FREQUENCY_WEIGHTING,
        user_activity_weighting=TRAINING_USER_ACTIVITY_WEIGHTING,
        known_user_score_centering_weight=(
            LIGHTFM_HYBRID_KNOWN_USER_SCORE_CENTERING_WEIGHT
        ),
    )
    with SessionLocal() as db:
        selected_user_ids = evaluation_user_ids(db, plans)
        dataset = restrict_to_evaluation_users(
            build_lightfm_dataset(db, data_cutoff_at=args.data_cutoff_at),
            user_ids=selected_user_ids,
        )
        validate_identity_dataset(dataset)
        item_export = export_item_features(db, args.ontology_build_id)
        supported_movie_ids = frozenset(
            dataset.movie_ids[int(index)]
            for index in set(dataset.interactions.tocoo(copy=False).col)
        )
        item_export = transform_item_feature_export(
            item_export,
            policy=LIGHTFM_HYBRID_FEATURE_REPRESENTATION,
            supported_movie_ids=supported_movie_ids,
            identity_weight=LIGHTFM_HYBRID_ITEM_IDENTITY_WEIGHT,
            semantic_weight=LIGHTFM_HYBRID_ITEM_SEMANTIC_WEIGHT,
            field_budgets=LIGHTFM_HYBRID_ITEM_FIELD_BUDGETS,
            keyword_weighting=LIGHTFM_HYBRID_ITEM_KEYWORD_WEIGHTING,
        )
        user_export = export_user_features(
            db,
            user_ids=dataset.user_ids,
            item_export=item_export,
            positive_interactions=dataset.positives,
        )
        user_export = transform_user_feature_export(
            user_export,
            policy=LIGHTFM_HYBRID_FEATURE_REPRESENTATION,
            identity_weight=LIGHTFM_HYBRID_USER_IDENTITY_WEIGHT,
            semantic_weight=LIGHTFM_HYBRID_USER_SEMANTIC_WEIGHT,
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
                "activation": "not_requested",
                "model_build_id": result.model_build_id,
                "artifact_path": str(artifact_path),
                "ontology_build_id": item_export.manifest.ontology_build_id,
                "diagnostics": result.diagnostics,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
