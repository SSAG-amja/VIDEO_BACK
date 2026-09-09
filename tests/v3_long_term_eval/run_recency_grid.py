from __future__ import annotations

import gc
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from lightfm import LightFM

from app.jobs.recsys.v3.features.feature_representation import (
    transform_user_feature_export,
)
from app.jobs.recsys.v3.features.user_feature_builder import build_user_feature_export
from app.services.recsys.v3.config import (
    LIGHTFM_HYBRID_EPOCHS,
    LIGHTFM_HYBRID_ITEM_ALPHA,
    LIGHTFM_HYBRID_LEARNING_RATE,
    LIGHTFM_HYBRID_MAX_SAMPLED,
    LIGHTFM_HYBRID_NO_COMPONENTS,
    LIGHTFM_HYBRID_USER_ALPHA,
)
from app.services.recsys.v3.retrieval.score_calibration import (
    mean_user_representation_components,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.run_ablation_experiment import (
    ROOT,
    attach_user_ids,
    build_training_signals,
    corrected_signals,
    current_artifact_path,
    evaluate_model,
    result_summary,
    signals_for_loss,
)


CENTERING_WEIGHT = 0.5
HALF_LIFE_DAYS = (60.0, 120.0, 180.0, 365.0, 730.0, None)
OUTPUT_DIR = ROOT / "outputs/recency_grid"


def main() -> None:
    artifact_path = current_artifact_path(500)
    artifact = load_runtime_hybrid_artifact(artifact_path)
    training_plans = load_plan(ROOT / "generated/cohorts/500/evaluation_plan.jsonl")
    evaluation_plans = load_plan(ROOT / "generated/cohorts/100/evaluation_plan.jsonl")
    attach_user_ids(training_plans, artifact.user_ids)
    attach_user_ids(evaluation_plans, artifact.user_ids)
    user_ids = tuple(int(value) for value in artifact.user_ids)
    movie_ids = tuple(int(value) for value in artifact.movie_ids)
    user_id_map = {value: index for index, value in enumerate(user_ids)}
    movie_id_map = {value: index for index, value in enumerate(movie_ids)}
    item_features = artifact.item_features
    item_export = SimpleNamespace(
        movie_ids=movie_ids,
        movie_id_map=movie_id_map,
        feature_tokens=artifact.item_feature_tokens,
        feature_token_map={
            token: index for index, token in enumerate(artifact.item_feature_tokens)
        },
        item_features=item_features,
        manifest=SimpleNamespace(
            ontology_build_id=artifact.ontology_build_id,
            ontology_source_hash=str(artifact.manifest["ontology"]["source_hash"]),
            export_hash=str(artifact.manifest["feature_exports"]["item_export_hash"]),
        ),
    )
    population_confidence = artifact.collaborative_population_confidence
    del artifact
    gc.collect()

    results = []
    for half_life in HALF_LIFE_DAYS:
        label = "none" if half_life is None else str(int(half_life))
        print(f"half_life_days={label}", flush=True)
        policy = "v3_no_decay" if half_life is None else "v3_decay"
        signals = build_training_signals(
            training_plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy=policy,
            positive_half_life_days=half_life,
        )
        raw_user_export = build_user_feature_export(
            user_ids=user_ids,
            explicit_genre_rows=(),
            favorite_rows=(),
            item_export=item_export,
            positive_interactions=signals.positives,
        )
        user_features = transform_user_feature_export(
            raw_user_export,
            policy="supported_identity_field_budgeted",
            identity_weight=2.0,
            semantic_weight=0.5,
        ).user_features
        signals = signals_for_loss(
            corrected_signals(
                signals,
                item_frequency_weighting=True,
                user_activity_weighting=True,
            ),
            loss="warp",
        )
        model = LightFM(
            no_components=LIGHTFM_HYBRID_NO_COMPONENTS,
            loss="warp",
            learning_rate=LIGHTFM_HYBRID_LEARNING_RATE,
            user_alpha=LIGHTFM_HYBRID_USER_ALPHA,
            item_alpha=LIGHTFM_HYBRID_ITEM_ALPHA,
            max_sampled=LIGHTFM_HYBRID_MAX_SAMPLED,
            random_state=42,
        )
        started = time.perf_counter()
        model.fit(
            signals.interactions,
            sample_weight=signals.sample_weights,
            user_features=user_features,
            item_features=item_features,
            epochs=LIGHTFM_HYBRID_EPOCHS,
            num_threads=1,
            verbose=False,
        )
        training_seconds = time.perf_counter() - started
        component_means = mean_user_representation_components(
            model,
            user_features,
            identity_feature_count=len(user_ids),
        )
        rows = evaluate_model(
            model,
            evaluation_plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            user_features=user_features,
            item_features=item_features,
            confidence_enabled=True,
            centering_weight=CENTERING_WEIGHT,
            population_confidence=population_confidence,
            component_means=component_means,
        )
        summary = result_summary(
            f"half_life_{label}",
            f"WARP hybrid, centering=0.5, positive half-life={label}",
            rows,
            training_seconds=training_seconds,
        )
        summary["positive_half_life_days"] = half_life
        summary["sample_weight_mean"] = float(signals.sample_weights.data.mean())
        results.append(summary)
        del model, user_features, raw_user_export
        gc.collect()

    best = max(
        results,
        key=lambda row: (row["ndcg_at_20"] + row["ndcg_at_30"], row["ndcg_at_20"]),
    )
    finite = [row for row in results if row["positive_half_life_days"] is not None]
    best_finite = max(
        finite,
        key=lambda row: (row["ndcg_at_20"] + row["ndcg_at_30"], row["ndcg_at_20"]),
    )
    payload = {
        "scope": "V3 long-term positive-action recency grid",
        "training_user_count": 500,
        "evaluation_user_count": 100,
        "centering_weight": CENTERING_WEIGHT,
        "collaborative_confidence": population_confidence,
        "item_frequency_weighting": "inverse_sqrt",
        "user_activity_weighting": "cap_at_median",
        "selection_rule": "maximum NDCG@20 + NDCG@30",
        "selected_overall": best["name"],
        "selected_finite": best_finite["name"],
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "recency_grid.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "recency_grid.md").write_text(markdown(payload), encoding="utf-8")
    print(
        f"selected_overall={best['name']} selected_finite={best_finite['name']}",
        flush=True,
    )


def markdown(payload: dict) -> str:
    lines = [
        "# V3 장기 취향 시간 감쇠 비교",
        "",
        "- centering: `0.5`",
        "- WARP, 영화 빈도 보정, 사용자 활동량 보정: 유지",
        "- 선택 기준: `NDCG@20 + NDCG@30` 최대",
        "",
        "| positive 반감기 | NDCG@10 | NDCG@20 | NDCG@30 | 전체 holdout | 평균 학습 가중치 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["results"]:
        value = row["positive_half_life_days"]
        label = "없음" if value is None else f"{int(value)}일"
        lines.append(
            f"| {label} | {row['ndcg_at_10']:.6f} | {row['ndcg_at_20']:.6f} | "
            f"{row['ndcg_at_30']:.6f} | {row['mean_full_holdout_ndcg']:.6f} | "
            f"{row['sample_weight_mean']:.6f} |"
        )
    lines.extend(
        (
            "",
            f"전체 최고: `{payload['selected_overall']}`",
            f"시간 감쇠 유지 조건 최고: `{payload['selected_finite']}`",
            "",
        )
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
