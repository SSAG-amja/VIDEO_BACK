from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.db.session import SessionLocal
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.engine_backend import (
    SUPPORTED_ENGINE_VERSIONS,
    load_evaluation_backend,
)
from tests.v3_long_term_eval.evaluation_dataset import (
    evaluation_user_ids,
    restrict_to_evaluation_users,
)


OUTPUT_ROOT = Path("tests/v3_long_term_eval/generated/models")
PLAN_PATH = Path("tests/v3_long_term_eval/generated/evaluation_plan.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train an inactive V3/V4 shadow model for long-term evaluation"
    )
    parser.add_argument("ontology_build_id", type=int)
    parser.add_argument(
        "--engine-version",
        choices=SUPPORTED_ENGINE_VERSIONS,
        default="v3",
    )
    parser.add_argument("--data-cutoff-at", type=datetime.fromisoformat)
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--num-threads", type=int)
    parser.add_argument(
        "--allow-health-gate-failure-for-ndcg",
        action="store_true",
        help=(
            "Persist an inactive evaluation artifact even when candidate "
            "concentration fails the production health gate"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    backend = load_evaluation_backend(args.engine_version)
    config_values = backend.config
    plans = load_plan(args.plan)
    config = backend.model_schemas.LightFMTrainingConfig(
        stage="hybrid_ontology",
        no_components=config_values.LIGHTFM_HYBRID_NO_COMPONENTS,
        epochs=(
            args.epochs
            if args.epochs is not None
            else config_values.LIGHTFM_HYBRID_EPOCHS
        ),
        learning_rate=config_values.LIGHTFM_HYBRID_LEARNING_RATE,
        user_alpha=config_values.LIGHTFM_HYBRID_USER_ALPHA,
        item_alpha=config_values.LIGHTFM_HYBRID_ITEM_ALPHA,
        max_sampled=config_values.LIGHTFM_HYBRID_MAX_SAMPLED,
        num_threads=(
            args.num_threads
            if args.num_threads is not None
            else config_values.LIGHTFM_HYBRID_NUM_THREADS
        ),
        item_frequency_weighting=config_values.TRAINING_ITEM_FREQUENCY_WEIGHTING,
        user_activity_weighting=config_values.TRAINING_USER_ACTIVITY_WEIGHTING,
        known_user_score_centering_weight=(
            config_values.LIGHTFM_HYBRID_KNOWN_USER_SCORE_CENTERING_WEIGHT
        ),
    )
    with SessionLocal() as db:
        selected_user_ids = evaluation_user_ids(db, plans)
        dataset = restrict_to_evaluation_users(
            backend.dataset_builder.build_lightfm_dataset(
                db,
                data_cutoff_at=args.data_cutoff_at,
            ),
            user_ids=selected_user_ids,
        )
        backend.trainer.validate_identity_dataset(dataset)
        item_export = backend.feature_builder.export_item_features(
            db,
            args.ontology_build_id,
        )
        supported_movie_ids = frozenset(
            dataset.movie_ids[int(index)]
            for index in set(dataset.interactions.tocoo(copy=False).col)
        )
        item_export = backend.feature_representation.transform_item_feature_export(
            item_export,
            policy=config_values.LIGHTFM_HYBRID_FEATURE_REPRESENTATION,
            supported_movie_ids=supported_movie_ids,
            identity_weight=config_values.LIGHTFM_HYBRID_ITEM_IDENTITY_WEIGHT,
            semantic_weight=config_values.LIGHTFM_HYBRID_ITEM_SEMANTIC_WEIGHT,
            field_budgets=config_values.LIGHTFM_HYBRID_ITEM_FIELD_BUDGETS,
            keyword_weighting=config_values.LIGHTFM_HYBRID_ITEM_KEYWORD_WEIGHTING,
        )
        user_export = backend.user_feature_builder.export_user_features(
            db,
            user_ids=dataset.user_ids,
            item_export=item_export,
            positive_interactions=dataset.positives,
        )
        user_export = backend.feature_representation.transform_user_feature_export(
            user_export,
            policy=config_values.LIGHTFM_HYBRID_USER_FEATURE_REPRESENTATION,
            identity_weight=config_values.LIGHTFM_HYBRID_USER_IDENTITY_WEIGHT,
            semantic_weight=config_values.LIGHTFM_HYBRID_USER_SEMANTIC_WEIGHT,
        )
        training_guard = (
            patch(
                f"app.jobs.recsys.{args.engine_version}.training.trainer."
                "assert_model_health"
            )
            if args.allow_health_gate_failure_for_ndcg
            else nullcontext()
        )
        with training_guard:
            result = backend.trainer.train_hybrid_model(
                dataset,
                item_export=item_export,
                user_export=user_export,
                config=config,
            )
        db.rollback()
    model_health = result.diagnostics["model_health"]
    health_gate_bypassed = (
        args.allow_health_gate_failure_for_ndcg
        and model_health["status"] != "pass"
    )
    publication_guard = (
        patch(
            f"app.jobs.recsys.{args.engine_version}.training.artifact_publisher."
            "assert_training_result_health"
        )
        if health_gate_bypassed
        else nullcontext()
    )
    with publication_guard:
        artifact_path = backend.artifact_publisher.publish_hybrid_artifact(
            result,
            args.output_root,
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "engine_version": args.engine_version,
                "activation": "not_requested",
                "model_build_id": result.model_build_id,
                "artifact_path": str(artifact_path),
                "ontology_build_id": item_export.manifest.ontology_build_id,
                "model_health_status": model_health["status"],
                "model_health_violations": model_health["violations"],
                "model_health_warnings": model_health.get("warnings", []),
                "health_gate_bypassed_for_ndcg": health_gate_bypassed,
                "diagnostics": result.diagnostics,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
