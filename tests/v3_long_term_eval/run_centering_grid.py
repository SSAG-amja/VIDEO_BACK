from __future__ import annotations

import json
from pathlib import Path

from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.run_ablation_experiment import (
    ROOT,
    attach_user_ids,
    current_artifact_path,
    evaluate_model,
    result_summary,
)


CENTERING_WEIGHTS = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9)
OUTPUT_DIR = ROOT / "outputs/centering_grid"


def main() -> None:
    artifact_path = current_artifact_path(500)
    artifact = load_runtime_hybrid_artifact(artifact_path)
    plans = load_plan(ROOT / "generated/cohorts/100/evaluation_plan.jsonl")
    attach_user_ids(plans, artifact.user_ids)
    user_id_map = {
        int(user_id): index for index, user_id in enumerate(artifact.user_ids)
    }
    movie_id_map = {
        int(movie_id): index for index, movie_id in enumerate(artifact.movie_ids)
    }

    results = []
    for weight in CENTERING_WEIGHTS:
        print(f"centering={weight:.2f}", flush=True)
        rows = evaluate_model(
            artifact.model,
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            user_features=artifact.user_features,
            item_features=artifact.item_features,
            confidence_enabled=True,
            centering_weight=weight,
            population_confidence=artifact.collaborative_population_confidence,
            component_means=(
                artifact.user_representation_component_means if weight > 0 else None
            ),
        )
        results.append(
            result_summary(
                f"centering_{weight:.2f}",
                f"Current V3 with collaborative confidence and centering={weight:.2f}",
                rows,
                training_seconds=0.0,
            )
        )

    best = max(
        results,
        key=lambda row: (row["ndcg_at_20"] + row["ndcg_at_30"], row["ndcg_at_20"]),
    )
    payload = {
        "scope": "current V3 score-centering grid",
        "training_user_count": 500,
        "evaluation_user_count": 100,
        "collaborative_confidence_enabled": True,
        "artifact_path": str(artifact_path),
        "selection_rule": "maximum NDCG@20 + NDCG@30",
        "selected": best["name"],
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "centering_grid.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "centering_grid.md").write_text(
        markdown(payload),
        encoding="utf-8",
    )
    print(f"selected={best['name']}", flush=True)


def markdown(payload: dict) -> str:
    lines = [
        "# V3 centering 비교",
        "",
        "- 학습 사용자: `500`명",
        "- 고정 평가 사용자: `100`명",
        "- 협업 신뢰도 감쇄: 유지",
        "- 선택 기준: `NDCG@20 + NDCG@30` 최대",
        "",
        "| centering | NDCG@10 | NDCG@20 | NDCG@30 | 전체 holdout |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["results"]:
        weight = row["name"].removeprefix("centering_")
        lines.append(
            f"| {weight} | {row['ndcg_at_10']:.6f} | {row['ndcg_at_20']:.6f} | "
            f"{row['ndcg_at_30']:.6f} | {row['mean_full_holdout_ndcg']:.6f} |"
        )
    lines.extend(("", f"선택값: `{payload['selected']}`", ""))
    return "\n".join(lines)


if __name__ == "__main__":
    main()
