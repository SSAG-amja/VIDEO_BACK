from __future__ import annotations

import argparse
import gc
import hashlib
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
from lightfm import LightFM
from scipy.sparse import csr_matrix, identity

from app.services.recsys.v3.config import (
    LIGHTFM_HYBRID_MAX_SAMPLED,
    TRAINING_RECENCY_HALF_LIFE_DAYS,
)
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.run_ablation_experiment import (
    build_training_signals,
    evaluate_model,
)


ROOT = Path("tests/v3_long_term_eval")
CASE_ID = "TUNE-21"
DEFAULT_PLAN = ROOT / "generated/matched_profiles/evaluation_plan.jsonl"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/lightfm_factorial_ndcg100"
DEFAULT_SOURCE_ARTIFACT = Path(
    "assets/ml_models/v3/"
    "hybrid-6e5ae61ef3bb-8ee76ae0cc79-51c5cf5ab1c1-"
    "32da0f11b507-6f0cb1a5dc05-7b869d3b"
)
SEEDS = (42, 43, 44)
USER_IDENTITY_WEIGHT = 2.0
SAVED_WEIGHT = 1.5
PINNED_WEIGHT = 1.0
BOOTSTRAP_SEED = 20260914
BOOTSTRAP_SAMPLES = 10_000
ANCHOR_TOLERANCE = 0.000_500
EXPECTED_ANCHORS = {
    "signal-baseline__identity-implicit__config-baseline": 0.873887,
    "signal-v3__identity-supported__config-v3": 0.879018,
}
FACTOR_LEVELS = {
    "signal": ("baseline", "v3"),
    "identity": ("implicit", "supported"),
    "config": ("baseline", "v3"),
}


@dataclass(frozen=True, slots=True)
class ModelConfig:
    no_components: int
    epochs: int
    learning_rate: float
    alpha: float = 1e-5


@dataclass(frozen=True, slots=True)
class FactorialVariant:
    signal: str
    identity: str
    config: str

    @property
    def name(self) -> str:
        return (
            f"signal-{self.signal}__identity-{self.identity}__config-{self.config}"
        )


MODEL_CONFIGS = {
    "baseline": ModelConfig(no_components=32, epochs=20, learning_rate=0.01),
    "v3": ModelConfig(no_components=16, epochs=60, learning_rate=0.025),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a LightFM 2x2x2 controlled experiment"
    )
    parser.add_argument("--case-id", default=CASE_ID)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument(
        "--source-artifact",
        type=Path,
        default=DEFAULT_SOURCE_ARTIFACT,
        help="Artifact used only for stable user/movie mappings",
    )
    parser.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    parser.add_argument("--num-threads", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--skip-anchor-check",
        action="store_true",
        help="Do not compare a new cohort with TUNE-21's historical score anchors",
    )
    parser.add_argument(
        "--comparison-only",
        action="store_true",
        help="Run only the pure baseline and locked V3 core final comparison",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = parse_seeds(args.seeds)
    if args.num_threads <= 0:
        raise SystemExit("num-threads must be positive")

    print("[1/5] load the fixed development cohort and artifact mappings", flush=True)
    plans = load_plan(args.plan)
    if len(plans) != 200:
        raise ValueError(
            f"the factorial experiment requires 200 development users, got {len(plans)}"
        )
    movie_ids, artifact_user_ids, manifest = load_artifact_mappings(
        args.source_artifact
    )
    assign_internal_user_ids(plans, artifact_user_ids)
    user_ids = tuple(int(plan["user_id"]) for plan in plans)
    user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
    movie_id_map = {int(movie_id): index for index, movie_id in enumerate(movie_ids)}
    validate_plan_movies(plans, movie_id_map)

    print("[2/5] build baseline and V3 training signals", flush=True)
    signal_matrices = build_signal_matrices(
        plans,
        user_id_map=user_id_map,
        movie_id_map=movie_id_map,
    )
    supported_items = supported_item_identity(
        signal_matrices["baseline"].interactions,
        movie_count=len(movie_ids),
    )
    supported_users = identity(len(user_ids), format="csr", dtype=np.float32)
    supported_users *= USER_IDENTITY_WEIGHT

    variants = comparison_variants() if args.comparison_only else factorial_variants()
    print(
        f"[3/5] train and evaluate {len(variants)} variants x {len(seeds)} seeds",
        flush=True,
    )
    rows: list[dict] = []
    timings: dict[str, list[float]] = {variant.name: [] for variant in variants}
    total_runs = len(variants) * len(seeds)
    run_index = 0
    for variant in variants:
        signals = signal_matrices[variant.signal]
        user_features, item_features = feature_matrices(
            variant.identity,
            supported_users=supported_users,
            supported_items=supported_items,
        )
        config = MODEL_CONFIGS[variant.config]
        for seed in seeds:
            run_index += 1
            print(
                f"  [{run_index:02d}/{total_runs}] {variant.name}, seed={seed}",
                flush=True,
            )
            model, elapsed = fit_model(
                signals.interactions,
                signals.sample_weights,
                config=config,
                seed=seed,
                num_threads=args.num_threads,
                user_features=user_features,
                item_features=item_features,
            )
            evaluated = evaluate_model(
                model,
                plans,
                user_id_map=user_id_map,
                movie_id_map=movie_id_map,
                user_features=user_features,
                item_features=item_features,
            )
            timings[variant.name].append(elapsed)
            for row in evaluated:
                candidate_count = int(row["candidate_count"])
                rows.append(
                    {
                        "variant": variant.name,
                        "signal": variant.signal,
                        "identity": variant.identity,
                        "config": variant.config,
                        "seed": seed,
                        "source_user_id": int(row["source_user_id"]),
                        "profile_type": profile_type_for(
                            plans, int(row["source_user_id"])
                        ),
                        "candidate_count": candidate_count,
                        "ndcg_at_100_percent": float(
                            row["ndcg"][str(candidate_count)]
                        ),
                        "ndcg_at_20_percent": float(row["ndcg_at_20_percent"]),
                    }
                )
            del model
            gc.collect()

    if args.comparison_only:
        print("[4/5] calculate the paired final comparison", flush=True)
        report = build_comparison_report(
            rows,
            variants=variants,
            timings=timings,
            seeds=seeds,
            plan=args.plan,
            source_artifact=args.source_artifact,
            manifest=manifest,
            movie_count=len(movie_ids),
            case_id=args.case_id,
        )
    else:
        print("[4/5] calculate controlled main and interaction effects", flush=True)
        report = build_report(
            rows,
            variants=variants,
            timings=timings,
            seeds=seeds,
            plan=args.plan,
            source_artifact=args.source_artifact,
            manifest=manifest,
            movie_count=len(movie_ids),
            case_id=args.case_id,
            require_anchor_check=not args.skip_anchor_check,
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        args.output_root
        / args.case_id
        / (
            f"{timestamp}_generalization_seeds-{'-'.join(map(str, seeds))}"
            if args.comparison_only
            else f"{timestamp}_development_seeds-{'-'.join(map(str, seeds))}"
        )
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    result_stem = "core_comparison_results" if args.comparison_only else "factorial_results"
    json_path = output_dir / f"{result_stem}.json"
    markdown_path = output_dir / f"{result_stem}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        comparison_report_markdown(report)
        if args.comparison_only
        else report_markdown(report),
        encoding="utf-8",
    )
    print(f"[5/5] complete: {markdown_path}", flush=True)
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": args.case_id,
                "anchors_reproduced": (
                    None
                    if args.comparison_only
                    else report["anchor_check"]["all_reproduced"]
                ),
                "gate_passed": (
                    report["gate"]["passed"] if args.comparison_only else None
                ),
                "json": str(json_path),
                "markdown": str(markdown_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def factorial_variants() -> tuple[FactorialVariant, ...]:
    return tuple(
        FactorialVariant(signal=signal, identity=identity_mode, config=config)
        for signal, identity_mode, config in product(
            FACTOR_LEVELS["signal"],
            FACTOR_LEVELS["identity"],
            FACTOR_LEVELS["config"],
        )
    )


def comparison_variants() -> tuple[FactorialVariant, ...]:
    return (
        FactorialVariant(signal="baseline", identity="implicit", config="baseline"),
        FactorialVariant(signal="v3", identity="supported", config="v3"),
    )


def parse_seeds(value: str) -> tuple[int, ...]:
    seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not seeds or len(set(seeds)) != len(seeds):
        raise argparse.ArgumentTypeError("seeds must be a non-empty unique integer list")
    return seeds


def load_artifact_mappings(path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    manifest_path = path / "manifest.json"
    movie_path = path / "movie_ids.npy"
    user_path = path / "user_ids.npy"
    for required in (manifest_path, movie_path, user_path):
        if not required.is_file():
            raise FileNotFoundError(f"artifact mapping file is missing: {required}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    movie_ids = np.load(movie_path, allow_pickle=False)
    user_ids = np.load(user_path, allow_pickle=False)
    if len(movie_ids) != int(manifest["dimensions"]["movies"]):
        raise ValueError("movie mapping count differs from the artifact manifest")
    if len(user_ids) != int(manifest["dimensions"]["users"]):
        raise ValueError("user mapping count differs from the artifact manifest")
    return movie_ids, user_ids, manifest


def assign_internal_user_ids(plans: list[dict], artifact_user_ids: np.ndarray) -> None:
    if len(artifact_user_ids) < len(plans):
        raise ValueError("source artifact has too few user identities")
    source_ids = [int(plan["source_user_id"]) for plan in plans]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("development plan contains duplicate source users")
    for plan, user_id in zip(plans, artifact_user_ids[: len(plans)], strict=True):
        plan["user_id"] = int(user_id)


def validate_plan_movies(plans: list[dict], movie_id_map: dict[int, int]) -> None:
    missing = {
        int(rating["movie_id"])
        for plan in plans
        for partition in ("train", "holdout")
        for rating in plan[partition]
        if int(rating["movie_id"]) not in movie_id_map
    }
    if missing:
        sample = ", ".join(map(str, sorted(missing)[:5]))
        raise ValueError(f"development movies are absent from the mapping: {sample}")


def build_signal_matrices(plans, *, user_id_map, movie_id_map):
    return {
        "baseline": build_training_signals(
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy="baseline",
        ),
        "v3": build_training_signals(
            plans,
            user_id_map=user_id_map,
            movie_id_map=movie_id_map,
            signal_policy="v3_decay",
            positive_half_life_days=float(
                TRAINING_RECENCY_HALF_LIFE_DAYS["saved"]
            ),
            passed_half_life_days=float(
                TRAINING_RECENCY_HALF_LIFE_DAYS["passed"]
            ),
            saved_weight=SAVED_WEIGHT,
            pinned_weight=PINNED_WEIGHT,
        ),
    }


def supported_item_identity(interactions, *, movie_count: int) -> csr_matrix:
    columns = np.unique(interactions.tocoo(copy=False).col).astype(np.int32)
    return csr_matrix(
        (
            np.ones(columns.size, dtype=np.float32),
            (columns, columns),
        ),
        shape=(movie_count, movie_count),
        dtype=np.float32,
    )


def feature_matrices(mode, *, supported_users, supported_items):
    if mode == "implicit":
        return None, None
    if mode == "supported":
        return supported_users, supported_items
    raise ValueError(f"unknown identity mode: {mode}")


def fit_model(
    interactions,
    sample_weights,
    *,
    config: ModelConfig,
    seed: int,
    num_threads: int,
    user_features,
    item_features,
):
    model = LightFM(
        no_components=config.no_components,
        loss="logistic",
        learning_rate=config.learning_rate,
        user_alpha=config.alpha,
        item_alpha=config.alpha,
        max_sampled=LIGHTFM_HYBRID_MAX_SAMPLED,
        random_state=seed,
    )
    started = time.perf_counter()
    model.fit(
        interactions,
        sample_weight=sample_weights,
        user_features=user_features,
        item_features=item_features,
        epochs=config.epochs,
        num_threads=num_threads,
        verbose=False,
    )
    return model, time.perf_counter() - started


def profile_type_for(plans: list[dict], source_user_id: int) -> str:
    for plan in plans:
        if int(plan["source_user_id"]) == source_user_id:
            return str(plan["profile_type"])
    raise KeyError(f"profile type is missing for source user {source_user_id}")


def build_report(
    rows,
    *,
    variants,
    timings,
    seeds,
    plan,
    source_artifact,
    manifest,
    movie_count,
    case_id=CASE_ID,
    require_anchor_check=True,
):
    summaries = [variant_summary(variant, rows, timings[variant.name]) for variant in variants]
    effects = {
        factor: factor_effect(rows, factor=factor)
        for factor in ("signal", "identity", "config")
    }
    interactions = {
        f"{left}_x_{right}": interaction_effect(rows, left=left, right=right)
        for left, right in (
            ("signal", "identity"),
            ("signal", "config"),
            ("identity", "config"),
        )
    }
    three_factor = three_factor_interaction(rows)
    anchors = anchor_check(
        summaries,
        expected_anchors=EXPECTED_ANCHORS if require_anchor_check else None,
    )
    return {
        "case_id": case_id,
        "scope": "LightFM long-term ranking 2x2x2 controlled experiment",
        "controls": {
            "development_user_count": len(
                {int(row["source_user_id"]) for row in rows}
            ),
            "movie_count": movie_count,
            "candidate_set": "each user's temporal 30% holdout only",
            "primary_metric": "NDCG@100%",
            "secondary_metric": "NDCG@20%",
            "seeds": list(seeds),
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "plan": str(plan),
            "plan_sha256": file_sha256(plan),
            "source_artifact": str(source_artifact),
            "source_model_build_id": str(manifest["model_build_id"]),
            "source_ontology_build_id": int(manifest["ontology"]["build_id"]),
            "active_bundle_changed": False,
            "models_persisted": False,
            "ontology_features_included": False,
            "serving_adjustments_included": False,
        },
        "factor_levels": FACTOR_LEVELS,
        "model_configs": {
            name: asdict(config) for name, config in MODEL_CONFIGS.items()
        },
        "variant_summaries": summaries,
        "main_effects": effects,
        "two_factor_interactions": interactions,
        "three_factor_interaction": three_factor,
        "anchor_check": anchors,
        "diagnosis": diagnosis(effects, interactions, three_factor, anchors),
        "rows": rows,
    }


def build_comparison_report(
    rows,
    *,
    variants,
    timings,
    seeds,
    plan,
    source_artifact,
    manifest,
    movie_count,
    case_id,
):
    summaries = [
        variant_summary(variant, rows, timings[variant.name]) for variant in variants
    ]
    effect = summarize_deltas(
        paired_deltas(
            rows,
            baseline={
                "signal": "baseline",
                "identity": "implicit",
                "config": "baseline",
            },
            changed={
                "signal": "v3",
                "identity": "supported",
                "config": "v3",
            },
        ),
        baseline="pure_baseline",
        changed="locked_v3_core",
    )
    return {
        "case_id": case_id,
        "scope": "LightFM long-term ranking locked-core generalization",
        "controls": {
            "generalization_user_count": len(
                {int(row["source_user_id"]) for row in rows}
            ),
            "movie_count": movie_count,
            "candidate_set": "each user's temporal 30% holdout only",
            "primary_metric": "NDCG@100%",
            "secondary_metric": "NDCG@20%",
            "seeds": list(seeds),
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "plan": str(plan),
            "plan_sha256": file_sha256(plan),
            "source_artifact": str(source_artifact),
            "source_model_build_id": str(manifest["model_build_id"]),
            "source_ontology_build_id": int(manifest["ontology"]["build_id"]),
            "active_bundle_changed": False,
            "models_persisted": False,
            "ontology_features_included": False,
            "serving_adjustments_included": False,
            "settings_selected_from_this_cohort": False,
        },
        "variants": {
            "pure_baseline": asdict(variants[0]),
            "locked_v3_core": asdict(variants[1]),
        },
        "model_configs": {
            name: asdict(config) for name, config in MODEL_CONFIGS.items()
        },
        "variant_summaries": summaries,
        "paired_effect": effect,
        "gate": {
            "passed": effect["verdict"] == "consistent_gain",
            "require_positive_bootstrap_interval": True,
            "require_all_seed_deltas_positive": True,
            "require_all_segment_deltas_positive": True,
        },
        "rows": rows,
    }


def variant_summary(variant, rows, timing_values):
    selected = [row for row in rows if row["variant"] == variant.name]
    return {
        "variant": variant.name,
        "signal": variant.signal,
        "identity": variant.identity,
        "config": variant.config,
        "ndcg_at_100_percent": statistics.mean(
            float(row["ndcg_at_100_percent"]) for row in selected
        ),
        "ndcg_at_20_percent": statistics.mean(
            float(row["ndcg_at_20_percent"]) for row in selected
        ),
        "mean_training_seconds": statistics.mean(timing_values),
    }


def factor_effect(rows: list[dict], *, factor: str) -> dict:
    baseline, changed = FACTOR_LEVELS[factor]
    other_factors = [name for name in FACTOR_LEVELS if name != factor]
    deltas = []
    contexts = {}
    for first in FACTOR_LEVELS[other_factors[0]]:
        for second in FACTOR_LEVELS[other_factors[1]]:
            fixed = {other_factors[0]: first, other_factors[1]: second}
            context_deltas = paired_deltas(
                rows,
                baseline={**fixed, factor: baseline},
                changed={**fixed, factor: changed},
            )
            deltas.extend(context_deltas)
            context_name = "__".join(
                f"{name}-{fixed[name]}" for name in other_factors
            )
            contexts[context_name] = statistics.mean(
                float(row["delta"]) for row in context_deltas
            )
    summary = summarize_deltas(deltas, baseline=baseline, changed=changed)
    summary["context_deltas"] = contexts
    return summary


def interaction_effect(rows: list[dict], *, left: str, right: str) -> dict:
    remaining = next(name for name in FACTOR_LEVELS if name not in (left, right))
    left_base, left_changed = FACTOR_LEVELS[left]
    right_base, right_changed = FACTOR_LEVELS[right]
    cells = index_rows(rows)
    values = []
    for remaining_level in FACTOR_LEVELS[remaining]:
        for seed, source_user_id in observation_keys(rows):
            common = {remaining: remaining_level}
            value = (
                metric_for(
                    cells,
                    {**common, left: left_changed, right: right_changed},
                    seed,
                    source_user_id,
                )
                - metric_for(
                    cells,
                    {**common, left: left_changed, right: right_base},
                    seed,
                    source_user_id,
                )
                - metric_for(
                    cells,
                    {**common, left: left_base, right: right_changed},
                    seed,
                    source_user_id,
                )
                + metric_for(
                    cells,
                    {**common, left: left_base, right: right_base},
                    seed,
                    source_user_id,
                )
            )
            template = next(
                row
                for row in rows
                if int(row["seed"]) == seed
                and int(row["source_user_id"]) == source_user_id
            )
            values.append(
                {
                    "seed": seed,
                    "source_user_id": source_user_id,
                    "profile_type": template["profile_type"],
                    "delta": value,
                }
            )
    return summarize_deltas(
        values,
        baseline=f"additive_{left}_{right}",
        changed=f"observed_{left}_{right}",
    )


def three_factor_interaction(rows: list[dict]) -> dict:
    cells = index_rows(rows)
    values = []
    for seed, source_user_id in observation_keys(rows):
        score = 0.0
        for signal_index, signal in enumerate(FACTOR_LEVELS["signal"]):
            for identity_index, identity_mode in enumerate(FACTOR_LEVELS["identity"]):
                for config_index, config in enumerate(FACTOR_LEVELS["config"]):
                    changed_count = signal_index + identity_index + config_index
                    coefficient = 1.0 if changed_count in (1, 3) else -1.0
                    score += coefficient * metric_for(
                        cells,
                        {
                            "signal": signal,
                            "identity": identity_mode,
                            "config": config,
                        },
                        seed,
                        source_user_id,
                    )
        template = next(
            row
            for row in rows
            if int(row["seed"]) == seed
            and int(row["source_user_id"]) == source_user_id
        )
        values.append(
            {
                "seed": seed,
                "source_user_id": source_user_id,
                "profile_type": template["profile_type"],
                "delta": score,
            }
        )
    return summarize_deltas(
        values,
        baseline="additive_signal_identity_config",
        changed="observed_signal_identity_config",
    )


def paired_deltas(rows, *, baseline, changed):
    cells = index_rows(rows)
    result = []
    for seed, source_user_id in observation_keys(rows):
        before = metric_for(cells, baseline, seed, source_user_id)
        after = metric_for(cells, changed, seed, source_user_id)
        template = next(
            row
            for row in rows
            if int(row["seed"]) == seed
            and int(row["source_user_id"]) == source_user_id
        )
        result.append(
            {
                "seed": seed,
                "source_user_id": source_user_id,
                "profile_type": template["profile_type"],
                "delta": after - before,
            }
        )
    return result


def index_rows(rows):
    return {
        (
            str(row["signal"]),
            str(row["identity"]),
            str(row["config"]),
            int(row["seed"]),
            int(row["source_user_id"]),
        ): float(row["ndcg_at_100_percent"])
        for row in rows
    }


def observation_keys(rows):
    return tuple(
        sorted(
            {
                (int(row["seed"]), int(row["source_user_id"]))
                for row in rows
            }
        )
    )


def metric_for(cells, factors, seed, source_user_id):
    key = (
        factors["signal"],
        factors["identity"],
        factors["config"],
        seed,
        source_user_id,
    )
    try:
        return cells[key]
    except KeyError as exc:
        raise ValueError(f"factorial result cell is missing: {key}") from exc


def summarize_deltas(deltas, *, baseline, changed):
    per_user = grouped_means(deltas, "source_user_id")
    seed = grouped_means(deltas, "seed")
    segment = grouped_means(deltas, "profile_type")
    values = list(per_user.values())
    confidence_interval = bootstrap_mean_ci(values)
    mean_delta = statistics.mean(values)
    return {
        "baseline": baseline,
        "changed": changed,
        "mean_delta_ndcg_at_100_percent": mean_delta,
        "paired_user_bootstrap_95_percent_ci": confidence_interval,
        "seed_deltas": seed,
        "segment_deltas": segment,
        "user_direction_counts": direction_counts(values),
        "verdict": effect_verdict(
            mean_delta,
            confidence_interval=confidence_interval,
            seed_deltas=seed,
            segment_deltas=segment,
        ),
    }


def grouped_means(rows, key):
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(float(row["delta"]))
    return {
        group: statistics.mean(values) for group, values in sorted(grouped.items())
    }


def bootstrap_mean_ci(values, *, samples=BOOTSTRAP_SAMPLES):
    vector = np.asarray(values, dtype=np.float64)
    if vector.size == 0:
        raise ValueError("bootstrap requires at least one value")
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    means = np.empty(samples, dtype=np.float64)
    for index in range(samples):
        means[index] = rng.choice(vector, size=vector.size, replace=True).mean()
    return {
        "lower": float(np.quantile(means, 0.025)),
        "upper": float(np.quantile(means, 0.975)),
        "samples": samples,
    }


def direction_counts(values, *, tolerance=1e-12):
    return {
        "improved": sum(value > tolerance for value in values),
        "declined": sum(value < -tolerance for value in values),
        "equal": sum(abs(value) <= tolerance for value in values),
    }


def effect_verdict(mean_delta, *, confidence_interval, seed_deltas, segment_deltas):
    all_seed_positive = all(value > 0 for value in seed_deltas.values())
    all_seed_negative = all(value < 0 for value in seed_deltas.values())
    all_segment_positive = all(value > 0 for value in segment_deltas.values())
    all_segment_negative = all(value < 0 for value in segment_deltas.values())
    if (
        mean_delta > 0
        and confidence_interval["lower"] > 0
        and all_seed_positive
        and all_segment_positive
    ):
        return "consistent_gain"
    if (
        mean_delta < 0
        and confidence_interval["upper"] < 0
        and all_seed_negative
        and all_segment_negative
    ):
        return "consistent_loss"
    return "uncertain"


def anchor_check(summaries, *, expected_anchors=EXPECTED_ANCHORS):
    if expected_anchors is None:
        return {
            "required": False,
            "tolerance": None,
            "all_reproduced": None,
            "variants": {},
        }
    actual = {row["variant"]: row["ndcg_at_100_percent"] for row in summaries}
    checks = {}
    for variant, expected in expected_anchors.items():
        delta = float(actual[variant]) - expected
        checks[variant] = {
            "expected": expected,
            "actual": float(actual[variant]),
            "delta": delta,
            "reproduced": abs(delta) <= ANCHOR_TOLERANCE,
        }
    return {
        "required": True,
        "tolerance": ANCHOR_TOLERANCE,
        "all_reproduced": all(item["reproduced"] for item in checks.values()),
        "variants": checks,
    }


def diagnosis(effects, interactions, three_factor, anchors):
    if anchors["required"] and not anchors["all_reproduced"]:
        return {
            "status": "invalid_anchor",
            "next_action": "Resolve evaluation reproduction before changing V3.",
        }
    losses = [
        (name, effect["mean_delta_ndcg_at_100_percent"])
        for name, effect in effects.items()
        if effect["verdict"] == "consistent_loss"
    ]
    interaction_losses = [
        (name, effect["mean_delta_ndcg_at_100_percent"])
        for name, effect in interactions.items()
        if effect["verdict"] == "consistent_loss"
    ]
    if three_factor["verdict"] != "uncertain":
        return {
            "status": "three_factor_interaction_isolated",
            "mean_delta_ndcg_at_100_percent": three_factor[
                "mean_delta_ndcg_at_100_percent"
            ],
            "next_action": (
                "Inspect context-specific effects before changing any single factor."
            ),
        }
    if losses:
        name, delta = min(losses, key=lambda item: item[1])
        return {
            "status": "main_effect_loss_isolated",
            "primary_factor": name,
            "mean_delta_ndcg_at_100_percent": delta,
            "next_action": f"Split and correct only the {name} factor.",
        }
    if interaction_losses:
        name, delta = min(interaction_losses, key=lambda item: item[1])
        return {
            "status": "interaction_loss_isolated",
            "primary_interaction": name,
            "mean_delta_ndcg_at_100_percent": delta,
            "next_action": f"Inspect only the {name} combination.",
        }
    return {
        "status": "no_consistent_core_loss",
        "next_action": "Stop scalar core tuning and redesign ontology score fusion.",
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def report_markdown(report):
    lines = [
        f"# {report['case_id']} LightFM Core Factorial Result",
        "",
        "## Controls",
        "",
        f"- Development users: `{report['controls']['development_user_count']}`",
        f"- Seeds: `{report['controls']['seeds']}`",
        "- Candidate set: each user's temporal 30% holdout only",
        "- Primary metric: NDCG@100%",
        "- Ontology features and serving adjustments: excluded",
        "- Active model and bundle: unchanged",
        "",
    ]
    if report["anchor_check"]["required"]:
        lines.extend(
            [
                "## Anchor Check",
                "",
                "| Variant | Expected | Actual | Delta | Reproduced |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for name, check in report["anchor_check"]["variants"].items():
            lines.append(
                f"| `{name}` | {check['expected']:.6f} | {check['actual']:.6f} | "
                f"{check['delta']:+.6f} | {check['reproduced']} |"
            )
    else:
        lines.extend(
            [
                "## Anchor Check",
                "",
                "- Not applicable: this run uses a newly selected development cohort.",
            ]
        )
    lines.extend(
        [
            "",
            "## Variants",
            "",
            "| Signal | Identity | Config | NDCG@100% | NDCG@20% | Train seconds |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in report["variant_summaries"]:
        lines.append(
            f"| {row['signal']} | {row['identity']} | {row['config']} | "
            f"{row['ndcg_at_100_percent']:.6f} | "
            f"{row['ndcg_at_20_percent']:.6f} | "
            f"{row['mean_training_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Main Effects",
            "",
            "| Factor | Change | NDCG@100% delta | 95% CI | Verdict |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for name, effect in report["main_effects"].items():
        interval = effect["paired_user_bootstrap_95_percent_ci"]
        lines.append(
            f"| {name} | {effect['baseline']} -> {effect['changed']} | "
            f"{effect['mean_delta_ndcg_at_100_percent']:+.6f} | "
            f"[{interval['lower']:+.6f}, {interval['upper']:+.6f}] | "
            f"{effect['verdict']} |"
        )
    lines.extend(
        [
            "",
            "## Context-specific Main Effects",
            "",
            "| Factor | Fixed context | NDCG@100% delta |",
            "| --- | --- | ---: |",
        ]
    )
    for name, effect in report["main_effects"].items():
        for context, delta in effect["context_deltas"].items():
            lines.append(f"| {name} | `{context}` | {delta:+.6f} |")
    lines.extend(
        [
            "",
            "## Two-factor Interactions",
            "",
            "| Interaction | NDCG@100% delta | 95% CI | Verdict |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for name, effect in report["two_factor_interactions"].items():
        interval = effect["paired_user_bootstrap_95_percent_ci"]
        lines.append(
            f"| {name} | {effect['mean_delta_ndcg_at_100_percent']:+.6f} | "
            f"[{interval['lower']:+.6f}, {interval['upper']:+.6f}] | "
            f"{effect['verdict']} |"
        )
    three_factor = report["three_factor_interaction"]
    interval = three_factor["paired_user_bootstrap_95_percent_ci"]
    lines.extend(
        [
            "",
            "## Three-factor Interaction",
            "",
            "| NDCG@100% delta | 95% CI | Verdict |",
            "| ---: | --- | --- |",
            (
                f"| {three_factor['mean_delta_ndcg_at_100_percent']:+.6f} | "
                f"[{interval['lower']:+.6f}, {interval['upper']:+.6f}] | "
                f"{three_factor['verdict']} |"
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Diagnosis",
            "",
            f"- Status: `{report['diagnosis']['status']}`",
            f"- Next action: {report['diagnosis']['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def comparison_report_markdown(report):
    effect = report["paired_effect"]
    interval = effect["paired_user_bootstrap_95_percent_ci"]
    lines = [
        f"# {report['case_id']} Locked V3 Core Generalization Result",
        "",
        "## Controls",
        "",
        f"- Generalization users: `{report['controls']['generalization_user_count']}`",
        f"- Seeds: `{report['controls']['seeds']}`",
        "- Candidate set: each user's temporal 30% holdout only",
        "- Primary metric: NDCG@100%",
        "- Settings selected from this cohort: no",
        "- Ontology features and serving adjustments: excluded",
        "- Active model and bundle: unchanged",
        "",
        "## Variants",
        "",
        "| Variant | Signal | Identity | Config | NDCG@100% | NDCG@20% |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    names = ("pure_baseline", "locked_v3_core")
    for name, row in zip(names, report["variant_summaries"], strict=True):
        lines.append(
            f"| {name} | {row['signal']} | {row['identity']} | {row['config']} | "
            f"{row['ndcg_at_100_percent']:.6f} | "
            f"{row['ndcg_at_20_percent']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Paired Effect",
            "",
            f"- Mean NDCG@100% delta: `{effect['mean_delta_ndcg_at_100_percent']:+.6f}`",
            (
                "- Paired-user bootstrap 95% CI: "
                f"`[{interval['lower']:+.6f}, {interval['upper']:+.6f}]`"
            ),
            f"- Seed deltas: `{effect['seed_deltas']}`",
            f"- Segment deltas: `{effect['segment_deltas']}`",
            f"- User direction counts: `{effect['user_direction_counts']}`",
            f"- Verdict: `{effect['verdict']}`",
            f"- Gate passed: `{report['gate']['passed']}`",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
