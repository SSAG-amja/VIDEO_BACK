from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from lightfm import LightFM
from scipy.sparse import coo_matrix

from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.metrics import evaluation_cutoffs, ndcg_at_k, rating_relevance
from tests.v3_long_term_eval.prepare import DEFAULT_USER_COUNTS, parse_user_counts


ROOT = Path("tests/v3_long_term_eval")
DEFAULT_OUTPUT_DIR = ROOT / "outputs/pure_lightfm"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a pure identity-only LightFM logistic baseline"
    )
    parser.add_argument(
        "--user-counts",
        type=parse_user_counts,
        default=DEFAULT_USER_COUNTS,
    )
    parser.add_argument("--no-components", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--user-alpha", type=float, default=1e-5)
    parser.add_argument("--item-alpha", type=float, default=1e-5)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--num-threads", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cohort_plans = {
        count: load_plan(
            ROOT / "generated/cohorts" / str(count) / "evaluation_plan.jsonl"
        )
        for count in args.user_counts
    }
    anchor_count = min(args.user_counts)
    anchor_plans = cohort_plans[anchor_count]
    maximum_plans = cohort_plans[max(args.user_counts)]
    movie_ids = fixed_movie_universe(maximum_plans, anchor_plans)
    movie_id_map = {movie_id: index for index, movie_id in enumerate(movie_ids)}
    args.output_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "loss": "logistic",
        "no_components": args.no_components,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "user_alpha": args.user_alpha,
        "item_alpha": args.item_alpha,
        "random_seed": args.random_seed,
        "num_threads": args.num_threads,
        "interaction_policy": {
            "passed_0.5_to_1.5": {"label": -1.0, "confidence": 1.0},
            "neutral_2.0_to_3.0": "excluded",
            "pinned_3.5_to_4.5": {"label": 1.0, "confidence": 1.0},
            "saved_5.0": {"label": 1.0, "confidence": 1.5},
        },
    }
    comparison_rows = []
    for cohort_size in args.user_counts:
        plans = cohort_plans[cohort_size]
        interactions, sample_weights, signal_counts = build_training_matrices(
            plans,
            movie_id_map=movie_id_map,
        )
        started = time.perf_counter()
        model = LightFM(
            no_components=args.no_components,
            loss="logistic",
            learning_rate=args.learning_rate,
            user_alpha=args.user_alpha,
            item_alpha=args.item_alpha,
            random_state=args.random_seed,
        )
        model.fit(
            interactions,
            sample_weight=sample_weights,
            epochs=args.epochs,
            num_threads=args.num_threads,
            verbose=False,
        )
        training_seconds = time.perf_counter() - started
        rows = evaluate_anchor_users(
            model,
            training_plans=plans,
            anchor_plans=anchor_plans,
            movie_id_map=movie_id_map,
            num_threads=args.num_threads,
        )
        stage_dir = args.output_dir / str(cohort_size)
        stage_dir.mkdir(parents=True, exist_ok=True)
        results_path = stage_dir / "results.jsonl"
        write_jsonl(results_path, rows)
        comparison_rows.append(
            comparison_row(
                cohort_size,
                rows,
                interaction_count=int(interactions.nnz),
                signal_counts=signal_counts,
                training_seconds=training_seconds,
                results_path=results_path,
            )
        )
        print(
            json.dumps(
                {
                    "status": "cohort_complete",
                    "training_user_count": cohort_size,
                    "evaluation_user_count": len(rows),
                    "training_seconds": training_seconds,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    payload = {
        "scope": "pure_identity_lightfm_holdout_ranking",
        "anchor_evaluation_user_count": anchor_count,
        "movie_universe_count": len(movie_ids),
        "config": config,
        "ndcg_relevance": "Same 1/2/3/4 rating grades as the V3 long-term test",
        "rows": comparison_rows,
    }
    json_path = args.output_dir / "scale_comparison.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = args.output_dir / "scale_comparison.md"
    markdown_path.write_text(comparison_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "summary": str(json_path),
                "markdown": str(markdown_path),
            },
            sort_keys=True,
        )
    )


def fixed_movie_universe(maximum_plans: list[dict], anchor_plans: list[dict]) -> tuple[int, ...]:
    movie_ids = {
        int(rating["movie_id"])
        for plan in maximum_plans
        for rating in plan["train"]
    }
    movie_ids.update(
        int(rating["movie_id"])
        for plan in anchor_plans
        for rating in plan["holdout"]
    )
    if not movie_ids:
        raise ValueError("pure LightFM evaluation has no movies")
    return tuple(sorted(movie_ids))


def build_training_matrices(
    plans: list[dict],
    *,
    movie_id_map: dict[int, int],
) -> tuple[coo_matrix, coo_matrix, dict[str, int]]:
    rows: list[int] = []
    columns: list[int] = []
    labels: list[float] = []
    confidences: list[float] = []
    signal_counts = {"passed": 0, "pinned": 0, "saved": 0, "neutral_excluded": 0}
    for user_index, plan in enumerate(plans):
        for rating in plan["train"]:
            value = float(rating["rating"])
            signal = lightfm_signal(value)
            if signal is None:
                signal_counts["neutral_excluded"] += 1
                continue
            label, confidence, action = signal
            rows.append(user_index)
            columns.append(movie_id_map[int(rating["movie_id"])])
            labels.append(label)
            confidences.append(confidence)
            signal_counts[action] += 1
    shape = (len(plans), len(movie_id_map))
    row_indices = np.asarray(rows, dtype=np.int32)
    column_indices = np.asarray(columns, dtype=np.int32)
    interactions = coo_matrix(
        (np.asarray(labels, dtype=np.float32), (row_indices, column_indices)),
        shape=shape,
        dtype=np.float32,
    )
    sample_weights = coo_matrix(
        (np.asarray(confidences, dtype=np.float32), (row_indices, column_indices)),
        shape=shape,
        dtype=np.float32,
    )
    return interactions, sample_weights, signal_counts


def lightfm_signal(rating: float) -> tuple[float, float, str] | None:
    if 0.5 <= rating <= 1.5:
        return -1.0, 1.0, "passed"
    if 3.5 <= rating <= 4.5:
        return 1.0, 1.0, "pinned"
    if rating == 5.0:
        return 1.0, 1.5, "saved"
    return None


def evaluate_anchor_users(
    model: LightFM,
    *,
    training_plans: list[dict],
    anchor_plans: list[dict],
    movie_id_map: dict[int, int],
    num_threads: int,
) -> list[dict]:
    training_user_indices = {
        int(plan["source_user_id"]): index for index, plan in enumerate(training_plans)
    }
    rows = []
    for plan in anchor_plans:
        source_user_id = int(plan["source_user_id"])
        user_index = training_user_indices[source_user_id]
        candidate_movie_ids = np.asarray(
            [int(rating["movie_id"]) for rating in plan["holdout"]],
            dtype=np.int64,
        )
        candidate_indices = np.asarray(
            [movie_id_map[int(movie_id)] for movie_id in candidate_movie_ids],
            dtype=np.int32,
        )
        scores = model.predict(
            user_index,
            candidate_indices,
            num_threads=num_threads,
        )
        order = np.lexsort((candidate_movie_ids, -scores))
        ranking = [int(movie_id) for movie_id in candidate_movie_ids[order]]
        relevance = {
            int(rating["movie_id"]): rating_relevance(float(rating["rating"]))
            for rating in plan["holdout"]
            if float(rating["rating"]) >= 3.5
        }
        candidate_count = len(candidate_movie_ids)
        rows.append(
            {
                "source_user_id": source_user_id,
                "candidate_count": candidate_count,
                "ranking": {
                    "ranked_movie_ids": ranking,
                    "ndcg": {
                        str(cutoff): ndcg_at_k(ranking, relevance, cutoff)
                        for cutoff in evaluation_cutoffs(candidate_count)
                    },
                },
            }
        )
    return rows


def comparison_row(
    cohort_size: int,
    rows: list[dict],
    *,
    interaction_count: int,
    signal_counts: dict[str, int],
    training_seconds: float,
    results_path: Path,
) -> dict:
    return {
        "training_user_count": cohort_size,
        "evaluation_user_count": len(rows),
        "interaction_count": interaction_count,
        "signal_counts": signal_counts,
        "training_seconds": training_seconds,
        "ndcg_at_10": mean_ndcg(rows, 10),
        "ndcg_at_20": mean_ndcg(rows, 20),
        "ndcg_at_30": mean_ndcg(rows, 30),
        "mean_full_holdout_ndcg": sum(
            float(row["ranking"]["ndcg"][str(row["candidate_count"])])
            for row in rows
        )
        / len(rows),
        "results_path": str(results_path),
    }


def mean_ndcg(rows: list[dict], cutoff: int) -> float:
    values = [float(row["ranking"]["ndcg"][str(cutoff)]) for row in rows]
    return sum(values) / len(values)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def comparison_markdown(payload: dict) -> str:
    lines = [
        "# 순수 LightFM 사용자 규모별 NDCG",
        "",
        "Ontology feature와 V3 정책 없이 사용자·영화 identity와 명시적 행동만 학습한다.",
        "",
        "| 학습 사용자 | 평가 사용자 | NDCG@10 | NDCG@20 | NDCG@30 | 전체 홀드아웃 NDCG |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['training_user_count']} | {row['evaluation_user_count']} "
            f"| {row['ndcg_at_10']:.6f} | {row['ndcg_at_20']:.6f} "
            f"| {row['ndcg_at_30']:.6f} "
            f"| {row['mean_full_holdout_ndcg']:.6f} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
