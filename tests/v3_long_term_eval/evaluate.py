from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
)
from app.services.recsys.v3.retrieval.score_calibration import (
    collaborative_adjusted_user_representations,
)
from app.services.recsys.v3.serving.model_store import (
    RuntimeHybridArtifact,
    load_runtime_hybrid_artifact,
)
from tests.v3_long_term_eval.metrics import evaluation_cutoffs, ndcg_at_k, rating_relevance


ROOT = Path("tests/v3_long_term_eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank each user's temporal 30% MovieLens holdout with V3"
    )
    parser.add_argument("model_artifact", type=Path)
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "generated/evaluation_plan.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    parser.add_argument(
        "--source-user-id",
        type=int,
        help="Evaluate only one MovieLens source user from the prepared plan",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plans = load_plan(args.plan)
    if args.source_user_id is not None:
        plans = [
            plan
            for plan in plans
            if int(plan["source_user_id"]) == args.source_user_id
        ]
        if not plans:
            raise SystemExit(
                f"source_user_id={args.source_user_id} is missing from {args.plan}"
            )
    if not plans:
        raise SystemExit(f"evaluation plan is empty: {args.plan}")

    artifact = load_runtime_hybrid_artifact(args.model_artifact)
    attach_database_user_ids(plans, artifact=artifact)
    score_started = time.perf_counter()
    rankings = score_holdout_candidates(artifact, plans)
    score_elapsed = time.perf_counter() - score_started

    rows = []
    for ordinal, plan in enumerate(plans, start=1):
        user_id = int(plan["user_id"])
        ranking = rankings[user_id]
        candidate_count = int(plan["candidate_count"])
        relevance = holdout_relevance(plan, artifact)
        rows.append(
            {
                "source_user_id": int(plan["source_user_id"]),
                "user_id": user_id,
                "profile_type": str(plan["profile_type"]),
                "candidate_count": candidate_count,
                "candidate_count_bucket": candidate_count_bucket(candidate_count),
                "train_rating_count": len(plan["train"]),
                "train_positive_count": int(plan["summary"]["train_positive_count"]),
                "holdout_relevant_count": len(relevance),
                "ranking": {
                    "returned_count": len(ranking),
                    "ndcg": ndcg_values(
                        ranking,
                        relevance,
                        cutoffs=evaluation_cutoffs(candidate_count),
                    ),
                    "ranked_movie_ids": ranking,
                },
            }
        )
        print(
            json.dumps(
                {
                    "status": "user_complete",
                    "ordinal": ordinal,
                    "total": len(plans),
                    "source_user_id": plan["source_user_id"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "long_term_evaluation_results.jsonl"
    with result_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = build_summary(
        rows,
        artifact=artifact,
        plan_path=args.plan,
        score_elapsed=score_elapsed,
    )
    summary_path = args.output_dir / "long_term_evaluation_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = args.output_dir / "long_term_evaluation_summary.md"
    markdown_path.write_text(summary_markdown(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "results": str(result_path),
                "summary": str(summary_path),
                "markdown": str(markdown_path),
            },
            sort_keys=True,
        )
    )


def load_plan(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"evaluation plan does not exist: {path}")
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if int(row.get("format_version", -1)) != 1:
                raise ValueError(f"unsupported evaluation plan at line {line_number}")
            if int(row.get("candidate_count", -1)) != len(row.get("holdout", ())):
                raise ValueError(
                    f"candidate count must equal holdout size at line {line_number}"
                )
            rows.append(row)
    source_ids = [int(row["source_user_id"]) for row in rows]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("evaluation plan contains duplicate users")
    return rows


def attach_database_user_ids(
    plans: list[dict],
    *,
    artifact: RuntimeHybridArtifact,
) -> None:
    with SessionLocal() as db:
        user_ids_by_email = dict(
            db.execute(
                select(User.email, User.id).where(
                    User.email.in_([str(item["email"]) for item in plans])
                )
            )
            .tuples()
            .all()
        )
    missing_users = sorted(
        str(item["email"])
        for item in plans
        if str(item["email"]) not in user_ids_by_email
    )
    if missing_users:
        raise SystemExit(
            f"{len(missing_users)} evaluation users are missing; apply seed SQL first"
        )
    for item in plans:
        item["user_id"] = int(user_ids_by_email[str(item["email"])])
        if artifact.user_index(int(item["user_id"])) is None:
            raise SystemExit(
                "evaluation artifact does not contain all seeded users; train a new "
                f"artifact after seeding (missing user_id={item['user_id']})"
            )


def score_holdout_candidates(
    artifact: RuntimeHybridArtifact,
    plans: list[dict],
) -> dict[int, list[int]]:
    rankings: dict[int, list[int]] = {}
    for plan in plans:
        user_id = int(plan["user_id"])
        user_index = artifact.user_index(user_id)
        assert user_index is not None
        confidence = assess_user_collaborative_confidence(
            positive_pair_count=int(plan["summary"]["train_positive_count"]),
            population_confidence=artifact.collaborative_population_confidence,
        ).effective_confidence
        user_biases, user_embeddings = collaborative_adjusted_user_representations(
            artifact.model,
            artifact.user_features[user_index],
            identity_feature_count=len(artifact.user_ids),
            collaborative_confidences=np.asarray([confidence], dtype=np.float32),
            centering_weight=artifact.known_user_score_centering_weight,
            component_means=artifact.user_representation_component_means,
        )
        candidate_movie_ids = np.asarray(
            [int(item["movie_id"]) for item in plan["holdout"]],
            dtype=np.int64,
        )
        if len(candidate_movie_ids) != len(set(candidate_movie_ids.tolist())):
            raise ValueError(f"holdout contains duplicate movies user_id={user_id}")
        item_indices = [
            artifact.movie_index(int(movie_id)) for movie_id in candidate_movie_ids
        ]
        if any(index is None for index in item_indices):
            raise ValueError(f"holdout movie is missing from artifact user_id={user_id}")
        item_biases, item_embeddings = artifact.model.get_item_representations(
            artifact.item_features[np.asarray(item_indices, dtype=np.int64)]
        )
        scores = np.asarray(item_embeddings, dtype=np.float32) @ np.asarray(
            user_embeddings[0],
            dtype=np.float32,
        )
        scores += float(user_biases[0])
        scores += (
            (1.0 - artifact.known_user_score_centering_weight)
            * np.asarray(item_biases, dtype=np.float32)
        )
        order = np.lexsort((candidate_movie_ids, -scores))
        rankings[user_id] = [int(movie_id) for movie_id in candidate_movie_ids[order]]
    return rankings


def holdout_relevance(plan: dict, artifact: RuntimeHybridArtifact) -> dict[int, float]:
    return {
        int(item["movie_id"]): rating_relevance(float(item["rating"]))
        for item in plan["holdout"]
        if float(item["rating"]) >= 3.5
        and artifact.movie_index(int(item["movie_id"])) is not None
    }


def exact_top_k_indices(scores: np.ndarray, movie_ids: np.ndarray, top_k: int) -> np.ndarray:
    valid = np.flatnonzero(np.isfinite(scores))
    if valid.size > top_k:
        valid_scores = scores[valid]
        partition = np.argpartition(valid_scores, -top_k)[-top_k:]
        threshold = np.min(valid_scores[partition])
        higher = valid[valid_scores > threshold]
        tied = valid[valid_scores == threshold]
        tied = tied[np.argsort(movie_ids[tied], kind="stable")[: top_k - higher.size]]
        valid = np.concatenate((higher, tied))
    order = np.lexsort((movie_ids[valid], -scores[valid]))
    return valid[order]


def build_summary(
    rows: list[dict],
    *,
    artifact: RuntimeHybridArtifact,
    plan_path: Path,
    score_elapsed: float,
) -> dict:
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": "long_term_holdout_ranking",
        "model_build_id": artifact.model_build_id,
        "ontology_build_id": artifact.ontology_build_id,
        "plan_path": str(plan_path),
        "user_count": len(rows),
        "candidate_count_range": [
            min(int(row["candidate_count"]) for row in rows),
            max(int(row["candidate_count"]) for row in rows),
        ],
        "score_elapsed_seconds": score_elapsed,
        "score_seconds_per_user": score_elapsed / len(rows),
        "overall": aggregate_rows(rows),
        "by_profile_type": grouped_aggregates(rows, "profile_type"),
        "by_candidate_count_bucket": grouped_aggregates(
            rows,
            "candidate_count_bucket",
        ),
        "interpretation": {
            "candidate_set": "Exactly the movies in each user's temporal 30% holdout.",
            "predicted_order": "Descending V3 LightFM hybrid score within that holdout.",
            "ideal_order": "Descending MovieLens relevance grade within the same holdout.",
            "ndcg_relevance": "MovieLens 3.5/4.0/4.5/5.0 map to grades 1/2/3/4.",
            "cutoffs": "NDCG is measured every 10 ranks and at the exact holdout size.",
        },
    }


def grouped_aggregates(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {name: aggregate_rows(values) for name, values in sorted(groups.items())}


def aggregate_rows(rows: list[dict]) -> dict:
    values_by_cutoff: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        for cutoff, value in row["ranking"]["ndcg"].items():
            values_by_cutoff[int(cutoff)].append(float(value))
    return {
        "user_count": len(rows),
        "mean_candidate_count": mean(
            [float(row["candidate_count"]) for row in rows]
        ),
        "mean_holdout_relevant_count": mean(
            [float(row["holdout_relevant_count"]) for row in rows]
        ),
        "mean_ndcg": {
            str(cutoff): mean(values)
            for cutoff, values in sorted(values_by_cutoff.items())
        },
        "ndcg_user_count": {
            str(cutoff): len(values)
            for cutoff, values in sorted(values_by_cutoff.items())
        },
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ndcg_values(
    ranking: list[int],
    relevance: dict[int, float],
    *,
    cutoffs: tuple[int, ...],
) -> dict[str, float]:
    return {
        str(cutoff): ndcg_at_k(ranking, relevance, cutoff)
        for cutoff in cutoffs
    }


def candidate_count_bucket(count: int) -> str:
    if count < 50:
        return "under-50"
    if count < 100:
        return "50-99"
    if count < 200:
        return "100-199"
    return "200-plus"


def summary_markdown(summary: dict) -> str:
    lines = [
        "# V3 Long-term Holdout Ranking Evaluation",
        "",
        f"- model: `{summary['model_build_id']}`",
        f"- ontology build: `{summary['ontology_build_id']}`",
        f"- users: `{summary['user_count']}`",
        f"- holdout candidates: `{summary['candidate_count_range'][0]}-{summary['candidate_count_range'][1]}`",
        "",
        "## Overall",
        "",
        metric_table(summary["overall"]),
        "",
        "## Profile Types",
        "",
    ]
    for name, values in summary["by_profile_type"].items():
        lines.extend((f"### {name}", "", metric_table(values), ""))
    lines.extend(("## Candidate Count Buckets", ""))
    for name, values in summary["by_candidate_count_bucket"].items():
        lines.extend((f"### {name}", "", metric_table(values), ""))
    lines.extend(
        (
            "## Interpretation Boundaries",
            "",
            "- Every ranked movie comes from the user's temporal 30% holdout.",
            "- The model orders those movies without searching the full catalog.",
            "- This measures ranking quality, not full-catalog candidate retrieval.",
            "",
        )
    )
    return "\n".join(lines)


def metric_table(values: dict) -> str:
    rows = [
        "| cutoff | eligible users | mean NDCG |",
        "| ---: | ---: | ---: |",
    ]
    for cutoff, ndcg in values["mean_ndcg"].items():
        rows.append(
            f"| {cutoff} | {values['ndcg_user_count'][cutoff]} | {ndcg:.6f} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    main()
