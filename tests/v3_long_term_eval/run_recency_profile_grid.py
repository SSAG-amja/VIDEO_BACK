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
from tests.v3_long_term_eval.prepare_recency_profiles import PROFILE_TYPES
from tests.v3_long_term_eval.run_ablation_experiment import (
    ROOT,
    build_training_signals,
    corrected_signals,
    current_artifact_path,
    evaluate_model,
    result_summary,
    signals_for_loss,
)


CENTERING_WEIGHT = 0.5
HALF_LIFE_DAYS = (60.0, 120.0, 180.0, 365.0, 730.0, None)
PLAN_PATH = ROOT / "generated/recency_profiles/evaluation_plan.jsonl"
OUTPUT_DIR = ROOT / "outputs/recency_profile_grid"


def main() -> None:
    plans = load_plan(PLAN_PATH)
    plans_by_profile = {
        profile_type: [
            plan for plan in plans if str(plan["profile_type"]) == profile_type
        ]
        for profile_type in PROFILE_TYPES
    }
    artifact = load_runtime_hybrid_artifact(current_artifact_path(500))
    if len(plans) > len(artifact.user_ids):
        raise ValueError("profile cohort exceeds available shadow artifact user identities")
    user_ids = tuple(int(value) for value in artifact.user_ids[: len(plans)])
    for plan, user_id in zip(plans, user_ids, strict=True):
        plan["user_id"] = user_id
    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    movie_ids = tuple(int(value) for value in artifact.movie_ids)
    movie_id_map = {movie_id: index for index, movie_id in enumerate(movie_ids)}
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
        signals = build_training_signals(
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy="v3_no_decay" if half_life is None else "v3_decay",
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
        overall_rows = evaluate_model(
            model,
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            user_features=user_features,
            item_features=item_features,
            confidence_enabled=True,
            centering_weight=CENTERING_WEIGHT,
            population_confidence=population_confidence,
            component_means=component_means,
        )
        profile_results = {}
        for profile_type, profile_plans in plans_by_profile.items():
            profile_rows = evaluate_model(
                model,
                profile_plans,
                user_id_map=user_id_map,
                movie_id_map=movie_id_map,
                user_features=user_features,
                item_features=item_features,
                confidence_enabled=True,
                centering_weight=CENTERING_WEIGHT,
                population_confidence=population_confidence,
                component_means=component_means,
            )
            profile_results[profile_type] = result_summary(
                profile_type,
                profile_type,
                profile_rows,
                training_seconds=training_seconds,
            )
        summary = result_summary(
            f"half_life_{label}",
            f"centering=0.5, positive half-life={label}",
            overall_rows,
            training_seconds=training_seconds,
        )
        summary["positive_half_life_days"] = half_life
        summary["by_profile"] = profile_results
        results.append(summary)
        del model, user_features, raw_user_export
        gc.collect()

    winners = {
        scope: winner(results, scope)
        for scope in ("overall", *PROFILE_TYPES)
    }
    payload = {
        "scope": "V3 positive recency by taste breadth and temporal stability",
        "training_and_evaluation_user_count": len(plans),
        "users_per_profile": {
            profile_type: len(values)
            for profile_type, values in plans_by_profile.items()
        },
        "centering_weight": CENTERING_WEIGHT,
        "fixed_population_confidence": population_confidence,
        "selection_rule": "maximum NDCG@20 + NDCG@30",
        "winners": winners,
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "recency_profile_grid.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "recency_profile_grid.md").write_text(
        markdown(payload),
        encoding="utf-8",
    )
    print(json.dumps(winners, ensure_ascii=False, sort_keys=True), flush=True)


def winner(results: list[dict], scope: str) -> str:
    def scores(row: dict) -> tuple[float, float]:
        values = row if scope == "overall" else row["by_profile"][scope]
        return (
            float(values["ndcg_at_20"]) + float(values["ndcg_at_30"]),
            float(values["ndcg_at_20"]),
        )

    return str(max(results, key=scores)["name"])


def markdown(payload: dict) -> str:
    lines = [
        "# V3 사용자 유형별 장기 취향 반감기 비교",
        "",
        "- 세 유형 각 100명을 하나의 300명 모델에 함께 학습하고 같은 사용자를 평가했다.",
        "- centering은 `0.5`, 나머지 V3 학습 보정과 ontology feature는 동일하다.",
        "- 선택 기준은 `NDCG@20 + NDCG@30`이다.",
        "",
        "| 반감기 | 전체 @20 | 전체 @30 | 집중·안정 @20 | 집중·안정 @30 | 다취향·안정 @20 | 다취향·안정 @30 | 취향변화 @20 | 취향변화 @30 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["results"]:
        value = row["positive_half_life_days"]
        label = "없음" if value is None else f"{int(value)}일"
        focused = row["by_profile"]["stable_focused"]
        diverse = row["by_profile"]["stable_diverse"]
        changing = row["by_profile"]["changing"]
        lines.append(
            f"| {label} | {row['ndcg_at_20']:.6f} | {row['ndcg_at_30']:.6f} | "
            f"{focused['ndcg_at_20']:.6f} | {focused['ndcg_at_30']:.6f} | "
            f"{diverse['ndcg_at_20']:.6f} | {diverse['ndcg_at_30']:.6f} | "
            f"{changing['ndcg_at_20']:.6f} | {changing['ndcg_at_30']:.6f} |"
        )
    lines.extend(("", "## 유형별 최고", ""))
    for scope, selected in payload["winners"].items():
        lines.append(f"- {scope}: `{selected}`")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
