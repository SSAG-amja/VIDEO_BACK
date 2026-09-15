from __future__ import annotations

import argparse
import gc
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.sparse import identity

from app.db.session import SessionLocal
from app.services.recsys.v3.config import TRAINING_RECENCY_HALF_LIFE_DAYS
from app.services.recsys.v3.profiles.profile_builder import load_graph_profile_edges
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.run_ablation_experiment import (
    build_training_signals,
    evaluate_model,
)
from tests.v3_long_term_eval.run_lightfm_factorial import (
    MODEL_CONFIGS,
    PINNED_WEIGHT,
    SAVED_WEIGHT,
    USER_IDENTITY_WEIGHT,
    assign_internal_user_ids,
    fit_model,
    supported_item_identity,
    validate_plan_movies,
)
from tests.v3_long_term_eval.run_ontology_fusion_experiment import (
    FusionVariant,
    build_ontology_contexts,
    comparison_effect,
    file_sha256,
    load_ontology_artifact,
    positive_training_movie_ids,
    evaluate_fusion_variants,
)


ROOT = Path("tests/v3_long_term_eval")
DEFAULT_PROFILE_ROOT = ROOT / "generated/final_ontology_generalization_profiles"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/final_generalization_ndcg100"
DEFAULT_SOURCE_ARTIFACT = Path(
    "assets/ml_models/v3/"
    "hybrid-6e5ae61ef3bb-8ee76ae0cc79-51c5cf5ab1c1-"
    "32da0f11b507-6f0cb1a5dc05-7b869d3b"
)
SEEDS = (42, 43, 44)
V3_VARIANTS = (
    FusionVariant("v3_core", "current", "none", 1.0, 0.0),
    FusionVariant(
        "v3_core_bounded_ontology_75_25",
        "current",
        "bounded_raw",
        0.75,
        0.25,
    ),
)
ENDPOINTS = (
    "pure_lightfm",
    "v3_core",
    "v3_core_bounded_ontology_75_25",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare pure LightFM, locked V3 core and bounded ontology fusion"
    )
    parser.add_argument(
        "--development-a",
        type=Path,
        default=DEFAULT_PROFILE_ROOT / "development_a/evaluation_plan.jsonl",
    )
    parser.add_argument(
        "--development-b",
        type=Path,
        default=DEFAULT_PROFILE_ROOT / "development_b/evaluation_plan.jsonl",
    )
    parser.add_argument("--source-artifact", type=Path, default=DEFAULT_SOURCE_ARTIFACT)
    parser.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    parser.add_argument("--num-threads", type=int, default=1)
    parser.add_argument("--expected-users-per-cohort", type=int, default=300)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--case-id", default="TUNE-29")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = parse_seeds(args.seeds)
    if args.num_threads <= 0 or args.expected_users_per_cohort <= 0:
        raise SystemExit("num-threads and expected-users-per-cohort must be positive")

    print("[1/6] load untouched final cohorts and ontology artifact", flush=True)
    artifact, artifact_user_ids = load_ontology_artifact(args.source_artifact)
    cohort_paths = {
        "final_a": args.development_a,
        "final_b": args.development_b,
    }
    cohorts = {name: load_plan(path) for name, path in cohort_paths.items()}
    movie_id_map = {
        int(movie_id): index for index, movie_id in enumerate(artifact.movie_ids)
    }
    for name, plans in cohorts.items():
        if len(plans) != args.expected_users_per_cohort:
            raise ValueError(
                f"{name} requires {args.expected_users_per_cohort} users, got {len(plans)}"
            )
        assign_internal_user_ids(plans, artifact_user_ids)
        validate_plan_movies(plans, movie_id_map)

    print("[2/6] load graph edges and build long-term ontology profiles", flush=True)
    positive_movie_ids = frozenset(
        movie_id
        for plans in cohorts.values()
        for plan in plans
        for movie_id in positive_training_movie_ids(plan)
    )
    with SessionLocal() as db:
        edges_by_movie = load_graph_profile_edges(
            db,
            ontology_build_id=artifact.ontology_build_id,
            movie_ids=positive_movie_ids,
        )
        db.rollback()
    ontology_contexts = {
        name: {
            "current": build_ontology_contexts(
                plans,
                artifact=artifact,
                edges_by_movie=edges_by_movie,
            )
        }
        for name, plans in cohorts.items()
    }

    total_runs = len(cohorts) * len(seeds) * 2
    print(f"[3/6] train and score {total_runs} paired LightFM models", flush=True)
    rows: list[dict] = []
    training_seconds = {endpoint: [] for endpoint in ENDPOINTS[:2]}
    run_index = 0
    for cohort_name, plans in cohorts.items():
        user_ids = tuple(int(plan["user_id"]) for plan in plans)
        user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
        baseline_signals = build_training_signals(
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy="baseline",
        )
        v3_signals = build_training_signals(
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy="v3_decay",
            positive_half_life_days=float(
                TRAINING_RECENCY_HALF_LIFE_DAYS["saved"]
            ),
            passed_half_life_days=float(TRAINING_RECENCY_HALF_LIFE_DAYS["passed"]),
            saved_weight=SAVED_WEIGHT,
            pinned_weight=PINNED_WEIGHT,
        )
        supported_users = identity(
            len(user_ids), format="csr", dtype=np.float32
        )
        supported_users *= USER_IDENTITY_WEIGHT
        supported_items = supported_item_identity(
            v3_signals.interactions,
            movie_count=len(artifact.movie_ids),
        )

        for seed in seeds:
            run_index += 1
            print(
                f"  [{run_index}/{total_runs}] {cohort_name}, seed={seed}, pure LightFM",
                flush=True,
            )
            pure_model, elapsed = fit_model(
                baseline_signals.interactions,
                baseline_signals.sample_weights,
                config=MODEL_CONFIGS["baseline"],
                seed=seed,
                num_threads=args.num_threads,
                user_features=None,
                item_features=None,
            )
            training_seconds["pure_lightfm"].append(elapsed)
            pure_rows = evaluate_model(
                pure_model,
                plans,
                user_id_map=user_id_map,
                movie_id_map=movie_id_map,
                user_features=None,
                item_features=None,
            )
            rows.extend(
                adapt_pure_rows(
                    pure_rows,
                    plans=plans,
                    cohort_name=cohort_name,
                    seed=seed,
                )
            )
            del pure_model
            gc.collect()

            run_index += 1
            print(
                f"  [{run_index}/{total_runs}] {cohort_name}, seed={seed}, V3 core",
                flush=True,
            )
            v3_model, elapsed = fit_model(
                v3_signals.interactions,
                v3_signals.sample_weights,
                config=MODEL_CONFIGS["v3"],
                seed=seed,
                num_threads=args.num_threads,
                user_features=supported_users,
                item_features=supported_items,
            )
            training_seconds["v3_core"].append(elapsed)
            rows.extend(
                evaluate_fusion_variants(
                    v3_model,
                    plans,
                    cohort_name=cohort_name,
                    seed=seed,
                    user_id_map=user_id_map,
                    movie_id_map=movie_id_map,
                    user_features=supported_users,
                    item_features=supported_items,
                    ontology_contexts=ontology_contexts[cohort_name],
                    variants=V3_VARIANTS,
                )
            )
            del v3_model
            gc.collect()

    print("[4/6] calculate paired endpoint effects", flush=True)
    report = build_report(
        rows,
        cohorts=cohorts,
        cohort_paths=cohort_paths,
        artifact=artifact,
        source_artifact=args.source_artifact,
        seeds=seeds,
        training_seconds=training_seconds,
        case_id=args.case_id,
    )

    print("[5/6] write immutable JSON and Markdown results", flush=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / args.case_id / f"{timestamp}_seeds-{'-'.join(map(str, seeds))}"
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "final_generalization_results.json"
    markdown_path = output_dir / "final_generalization_results.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report_markdown(report), encoding="utf-8")
    print(f"[6/6] complete: {markdown_path}", flush=True)
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": args.case_id,
                "gate_passed": report["gate"]["passed"],
                "json": str(json_path),
                "markdown": str(markdown_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def parse_seeds(value: str) -> tuple[int, ...]:
    seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not seeds or len(set(seeds)) != len(seeds):
        raise argparse.ArgumentTypeError("seeds must be a non-empty unique integer list")
    return seeds


def adapt_pure_rows(
    evaluated: list[dict],
    *,
    plans: list[dict],
    cohort_name: str,
    seed: int,
) -> list[dict]:
    profile_types = {
        int(plan["source_user_id"]): str(plan["profile_type"]) for plan in plans
    }
    return [
        {
            "cohort": cohort_name,
            "variant": "pure_lightfm",
            "profile_mode": "none",
            "ontology_normalization": "none",
            "excluded_families": [],
            "seed": seed,
            "source_user_id": int(row["source_user_id"]),
            "profile_type": profile_types[int(row["source_user_id"])],
            "candidate_count": int(row["candidate_count"]),
            "ndcg_at_100_percent": float(
                row["ndcg"][str(row["candidate_count"])]
            ),
            "ndcg_at_20_percent": float(row["ndcg_at_20_percent"]),
            "ontology_matched_candidate_count": 0,
            "model_ontology_rank_correlation": None,
        }
        for row in evaluated
    ]


def build_report(
    rows: list[dict],
    *,
    cohorts: dict[str, list[dict]],
    cohort_paths: dict[str, Path],
    artifact,
    source_artifact: Path,
    seeds: tuple[int, ...],
    training_seconds: dict[str, list[float]],
    case_id: str,
) -> dict:
    summaries = []
    for scope in ("all", *cohorts):
        for endpoint in ENDPOINTS:
            selected = [
                row
                for row in rows
                if row["variant"] == endpoint
                and (scope == "all" or row["cohort"] == scope)
            ]
            summaries.append(
                {
                    "scope": scope,
                    "endpoint": endpoint,
                    "ndcg_at_100_percent": statistics.mean(
                        float(row["ndcg_at_100_percent"]) for row in selected
                    ),
                    "ndcg_at_20_percent": statistics.mean(
                        float(row["ndcg_at_20_percent"]) for row in selected
                    ),
                }
            )
    effects = {
        "v3_core_vs_pure_lightfm": comparison_effect(
            rows,
            baseline="pure_lightfm",
            changed="v3_core",
            metric="ndcg_at_100_percent",
        ),
        "bounded_ontology_vs_v3_core": comparison_effect(
            rows,
            baseline="v3_core",
            changed="v3_core_bounded_ontology_75_25",
            metric="ndcg_at_100_percent",
        ),
        "full_v3_vs_pure_lightfm": comparison_effect(
            rows,
            baseline="pure_lightfm",
            changed="v3_core_bounded_ontology_75_25",
            metric="ndcg_at_100_percent",
        ),
    }
    gate_checks = {
        name: effect["verdict"] == "consistent_gain"
        and all(delta > 0.0 for delta in effect["cohort_deltas"].values())
        for name, effect in effects.items()
    }
    return {
        "case_id": case_id,
        "scope": "final untouched-user long-term preference ranking comparison",
        "controls": {
            "users_per_cohort": {name: len(plans) for name, plans in cohorts.items()},
            "total_unique_users": len(
                {
                    int(plan["source_user_id"])
                    for plans in cohorts.values()
                    for plan in plans
                }
            ),
            "candidate_set": "each user's temporal 30% holdout only",
            "primary_metric": "NDCG@100%",
            "secondary_metric": "NDCG@20%",
            "seeds": list(seeds),
            "cohort_plans": {
                name: {"path": str(path), "sha256": file_sha256(path)}
                for name, path in cohort_paths.items()
            },
            "source_artifact": str(source_artifact),
            "source_model_build_id": str(artifact.manifest["model_build_id"]),
            "source_ontology_build_id": artifact.ontology_build_id,
            "active_bundle_changed": False,
            "models_persisted": False,
            "full_catalog_retrieval_included": False,
        },
        "endpoint_definitions": {
            "pure_lightfm": "baseline labels, implicit identity, baseline model config",
            "v3_core": "V3 labels/decay, supported identity, locked V3 model config",
            "v3_core_bounded_ontology_75_25": (
                "V3 core percentile 75% + field-budget bounded ontology raw score 25%"
            ),
        },
        "training_seconds": {
            name: {
                "mean": statistics.mean(values),
                "total": sum(values),
            }
            for name, values in training_seconds.items()
        },
        "endpoint_summaries": summaries,
        "paired_effects_ndcg_at_100_percent": effects,
        "gate": {
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
            "requires_positive_bootstrap_interval": True,
            "requires_all_seed_profile_and_cohort_deltas_positive": True,
        },
        "rows": rows,
    }


def report_markdown(report: dict) -> str:
    lines = [
        f"# {report['case_id']} Final Generalization Result",
        "",
        "## Controls",
        "",
        f"- Users: `{report['controls']['total_unique_users']}`",
        f"- Cohorts: `{report['controls']['users_per_cohort']}`",
        f"- Seeds: `{report['controls']['seeds']}`",
        "- Candidate set: each user's temporal 30% holdout movies",
        "- Primary metric: NDCG@100%",
        "- Full-catalog retrieval and serving policy: excluded",
        "- Active model and bundle: unchanged",
        "",
        "## Scores",
        "",
        "| Scope | Endpoint | NDCG@100% | NDCG@20% |",
        "| --- | --- | ---: | ---: |",
    ]
    for summary in report["endpoint_summaries"]:
        lines.append(
            f"| {summary['scope']} | {summary['endpoint']} | "
            f"{summary['ndcg_at_100_percent']:.6f} | "
            f"{summary['ndcg_at_20_percent']:.6f} |"
        )
    lines.extend(["", "## Paired Effects", ""])
    for name, effect in report["paired_effects_ndcg_at_100_percent"].items():
        interval = effect["paired_user_bootstrap_95_percent_ci"]
        lines.extend(
            [
                f"### {name}",
                "",
                f"- Delta: `{effect['mean_delta_ndcg_at_100_percent']:+.6f}`",
                f"- 95% CI: `[{interval['lower']:+.6f}, {interval['upper']:+.6f}]`",
                f"- Seed deltas: `{effect['seed_deltas']}`",
                f"- Profile deltas: `{effect['segment_deltas']}`",
                f"- Cohort deltas: `{effect['cohort_deltas']}`",
                f"- User directions: `{effect['user_direction_counts']}`",
                f"- Verdict: `{effect['verdict']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Gate",
            "",
            f"- Checks: `{report['gate']['checks']}`",
            f"- Passed: `{report['gate']['passed']}`",
            "",
            "This test measures ranking of fixed holdout movies, not full-catalog retrieval.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
