from __future__ import annotations

import argparse
import gc
import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
from scipy.sparse import csr_matrix, identity, load_npz

from app.db.session import SessionLocal
from app.services.recsys.v3.config import (
    PROFILE_FEATURE_TOP_K,
    TRAINING_RECENCY_HALF_LIFE_DAYS,
)
from app.services.recsys.v3.domain.behavior import SnapshotAction, SnapshotSignal
from app.services.recsys.v3.domain.feature_registry import (
    FeatureName,
    get_feature_definition,
)
from app.services.recsys.v3.domain.schemas import FeatureDirection, ProfileFeatureSignal
from app.services.recsys.v3.profiles.profile_builder import (
    aggregate_profile_features,
    load_graph_profile_edges,
    long_term_decay,
)
from app.services.recsys.v3.retrieval.ontology_feature_retriever import (
    build_budgeted_candidate_aggregate_rows,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import ProfileScope
from app.services.recsys.v3.retrieval.score_normalizer import percentile_normalize
from tests.v3_long_term_eval.evaluate import load_plan
from tests.v3_long_term_eval.metrics import ndcg_at_k, percentage_cutoff, rating_relevance
from tests.v3_long_term_eval.prepare import normalized_training_times
from tests.v3_long_term_eval.run_ablation_experiment import build_training_signals
from tests.v3_long_term_eval.run_lightfm_factorial import (
    MODEL_CONFIGS,
    PINNED_WEIGHT,
    SAVED_WEIGHT,
    USER_IDENTITY_WEIGHT,
    assign_internal_user_ids,
    fit_model,
    summarize_deltas,
    supported_item_identity,
    validate_plan_movies,
)


ROOT = Path("tests/v3_long_term_eval")
CASE_ID = "TUNE-24"
DEFAULT_DEVELOPMENT_A = ROOT / "generated/matched_profiles/evaluation_plan.jsonl"
DEFAULT_DEVELOPMENT_B = (
    ROOT / "generated/factorial_replication_profiles/evaluation_plan.jsonl"
)
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/ontology_fusion_ndcg100"
DEFAULT_SOURCE_ARTIFACT = Path(
    "assets/ml_models/v3/"
    "hybrid-6e5ae61ef3bb-8ee76ae0cc79-51c5cf5ab1c1-"
    "32da0f11b507-6f0cb1a5dc05-7b869d3b"
)
SEEDS = (42, 43, 44)


@dataclass(frozen=True, slots=True)
class FusionVariant:
    name: str
    profile_mode: str
    normalization: str
    personal_weight: float
    ontology_weight: float
    excluded_families: tuple[str, ...] = ()


BASE_FUSION_VARIANTS = (
    FusionVariant("model_only", "current", "none", 1.0, 0.0),
    FusionVariant("current_75_25", "current", "percentile", 0.75, 0.25),
)
DIAGNOSTIC_FUSION_VARIANTS = BASE_FUSION_VARIANTS + (
    FusionVariant("current_bounded_75_25", "current", "bounded_raw", 0.75, 0.25),
    FusionVariant(
        "repeated_evidence_percentile_75_25",
        "repeated_evidence",
        "percentile",
        0.75,
        0.25,
    ),
    FusionVariant(
        "repeated_evidence_bounded_75_25",
        "repeated_evidence",
        "bounded_raw",
        0.75,
        0.25,
    ),
)
ONTOLOGY_FAMILIES = tuple(
    feature.value
    for feature in (
        FeatureName.GENRE,
        FeatureName.KEYWORD,
        FeatureName.ACTOR,
        FeatureName.DIRECTOR,
        FeatureName.THEME,
        FeatureName.MOOD,
    )
)
FAMILY_ABLATION_BASELINE = FusionVariant(
    "current_bounded_75_25",
    "current",
    "bounded_raw",
    0.75,
    0.25,
)
FAMILY_ABLATION_VARIANTS = (
    BASE_FUSION_VARIANTS[0],
    FAMILY_ABLATION_BASELINE,
) + tuple(
    FusionVariant(
        f"current_bounded_without_{family}",
        "current",
        "bounded_raw",
        0.75,
        0.25,
        (family,),
    )
    for family in ONTOLOGY_FAMILIES
)
CONCENTRATION_DIAGNOSTIC_VARIANTS = (
    BASE_FUSION_VARIANTS[0],
    FAMILY_ABLATION_BASELINE,
)
PROFILE_MINIMUM_CONTRIBUTION_COUNTS = {
    "current": 1,
    "repeated_evidence": 2,
}


@dataclass(frozen=True, slots=True)
class OntologyArtifactView:
    movie_ids: np.ndarray
    item_features: csr_matrix
    item_semantic_feature_index: dict[str, int]
    manifest: dict

    @property
    def ontology_build_id(self) -> int:
        return int(self.manifest["ontology"]["build_id"])

    def movie_index(self, movie_id: int) -> int | None:
        position = int(np.searchsorted(self.movie_ids, int(movie_id)))
        if position >= self.movie_ids.size or int(self.movie_ids[position]) != int(movie_id):
            return None
        return position


@dataclass(frozen=True, slots=True)
class UserOntologyContext:
    source_user_id: int
    scores_by_movie: dict[int, float]
    profile_feature_count: int
    positive_movie_count: int
    graph_covered_positive_movie_count: int
    matched_candidate_count: int
    family_score_totals: dict[str, float]
    scores_by_family: dict[str, dict[int, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure the final V3 ontology component on fixed holdout movies"
    )
    parser.add_argument("--development-a", type=Path, default=DEFAULT_DEVELOPMENT_A)
    parser.add_argument("--development-b", type=Path, default=DEFAULT_DEVELOPMENT_B)
    parser.add_argument("--source-artifact", type=Path, default=DEFAULT_SOURCE_ARTIFACT)
    parser.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    parser.add_argument("--num-threads", type=int, default=1)
    parser.add_argument("--expected-users-per-cohort", type=int, default=200)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--case-id", default=CASE_ID)
    diagnostic_mode = parser.add_mutually_exclusive_group()
    diagnostic_mode.add_argument(
        "--diagnostic-grid",
        action="store_true",
        help="Compare profile breadth and ontology normalization as a 2x2 diagnostic",
    )
    diagnostic_mode.add_argument(
        "--family-ablation",
        action="store_true",
        help="Remove one ontology family at a time from bounded raw fusion",
    )
    diagnostic_mode.add_argument(
        "--concentration-diagnostic",
        action="store_true",
        help="Relate train-only genre concentration to bounded ontology ranking effect",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = parse_seeds(args.seeds)
    if args.concentration_diagnostic:
        variants = CONCENTRATION_DIAGNOSTIC_VARIANTS
    elif args.family_ablation:
        variants = FAMILY_ABLATION_VARIANTS
    elif args.diagnostic_grid:
        variants = DIAGNOSTIC_FUSION_VARIANTS
    else:
        variants = BASE_FUSION_VARIANTS
    if args.num_threads <= 0 or args.expected_users_per_cohort <= 0:
        raise SystemExit("num-threads and expected-users-per-cohort must be positive")

    print("[1/6] load development cohorts and field-budget ontology artifact", flush=True)
    artifact, artifact_user_ids = load_ontology_artifact(args.source_artifact)
    cohorts = {
        "development_a": load_plan(args.development_a),
        "development_b": load_plan(args.development_b),
    }
    for name, plans in cohorts.items():
        if len(plans) != args.expected_users_per_cohort:
            raise ValueError(
                f"{name} requires {args.expected_users_per_cohort} development users, "
                f"got {len(plans)}"
            )
        assign_internal_user_ids(plans, artifact_user_ids)
        movie_id_map = {
            int(movie_id): index for index, movie_id in enumerate(artifact.movie_ids)
        }
        validate_plan_movies(plans, movie_id_map)

    print("[2/6] load build-22 graph edges for positive training movies", flush=True)
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

    profile_modes = tuple(dict.fromkeys(variant.profile_mode for variant in variants))
    print("[3/6] build production-equivalent long-term ontology profiles", flush=True)
    ontology_contexts = {
        name: {
            profile_mode: build_ontology_contexts(
                plans,
                artifact=artifact,
                edges_by_movie=edges_by_movie,
                profile_mode=profile_mode,
                minimum_contribution_count=PROFILE_MINIMUM_CONTRIBUTION_COUNTS[
                    profile_mode
                ],
            )
            for profile_mode in profile_modes
        }
        for name, plans in cohorts.items()
    }

    print(
        f"[4/6] train the locked V3 core and score {len(variants)} fusion variants",
        flush=True,
    )
    rows: list[dict] = []
    training_seconds: dict[str, list[float]] = {name: [] for name in cohorts}
    total_runs = len(cohorts) * len(seeds)
    run_index = 0
    movie_id_map = {
        int(movie_id): index for index, movie_id in enumerate(artifact.movie_ids)
    }
    for cohort_name, plans in cohorts.items():
        user_ids = tuple(int(plan["user_id"]) for plan in plans)
        user_id_map = {user_id: index for index, user_id in enumerate(user_ids)}
        signals = build_training_signals(
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
        )
        supported_items = supported_item_identity(
            signals.interactions,
            movie_count=len(artifact.movie_ids),
        )
        supported_users = identity(
            len(user_ids), format="csr", dtype=np.float32
        )
        supported_users *= USER_IDENTITY_WEIGHT
        for seed in seeds:
            run_index += 1
            print(
                f"  [{run_index}/{total_runs}] {cohort_name}, seed={seed}",
                flush=True,
            )
            model, elapsed = fit_model(
                signals.interactions,
                signals.sample_weights,
                config=MODEL_CONFIGS["v3"],
                seed=seed,
                num_threads=args.num_threads,
                user_features=supported_users,
                item_features=supported_items,
            )
            training_seconds[cohort_name].append(elapsed)
            rows.extend(
                evaluate_fusion_variants(
                    model,
                    plans,
                    cohort_name=cohort_name,
                    seed=seed,
                    user_id_map=user_id_map,
                    movie_id_map=movie_id_map,
                    user_features=supported_users,
                    item_features=supported_items,
                    ontology_contexts=ontology_contexts[cohort_name],
                    variants=variants,
                )
            )
            del model
            gc.collect()

    print("[5/6] summarize paired effects and ontology coverage", flush=True)
    report = build_report(
        rows,
        cohorts=cohorts,
        ontology_contexts=ontology_contexts,
        training_seconds=training_seconds,
        seeds=seeds,
        source_artifact=args.source_artifact,
        artifact=artifact,
        variants=variants,
        case_id=args.case_id,
        development_paths={
            "development_a": args.development_a,
            "development_b": args.development_b,
        },
        experiment_mode=(
            "concentration_diagnostic"
            if args.concentration_diagnostic
            else "family_ablation"
            if args.family_ablation
            else "profile_normalization_grid"
            if args.diagnostic_grid
            else "baseline"
        ),
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        args.output_root
        / args.case_id
        / f"{timestamp}_development-a-b_seeds-{'-'.join(map(str, seeds))}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "ontology_fusion_results.json"
    markdown_path = output_dir / "ontology_fusion_results.md"
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


def load_ontology_artifact(
    path: Path,
) -> tuple[OntologyArtifactView, np.ndarray]:
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    movie_ids = np.load(path / "movie_ids.npy", allow_pickle=False)
    user_ids = np.load(path / "user_ids.npy", allow_pickle=False)
    item_features = load_npz(path / "item_features.npz").tocsr()
    item_tokens = tuple(joblib.load(path / "item_feature_tokens.joblib"))
    dimensions = manifest["dimensions"]
    if item_features.shape != (
        int(dimensions["movies"]),
        int(dimensions["item_features"]),
    ):
        raise ValueError("item feature dimensions differ from the artifact manifest")
    if len(movie_ids) != int(dimensions["movies"]) or len(item_tokens) != item_features.shape[1]:
        raise ValueError("artifact item mappings are inconsistent")
    if manifest.get("feature_exports", {}).get("item_representation_policy") != (
        "supported_identity_field_budgeted"
    ):
        raise ValueError(
            "ontology fusion experiment requires the field-budgeted item representation"
        )
    semantic_index = {
        str(token): index
        for index, token in enumerate(item_tokens[len(movie_ids) :], start=len(movie_ids))
    }
    return (
        OntologyArtifactView(
            movie_ids=movie_ids,
            item_features=item_features,
            item_semantic_feature_index=semantic_index,
            manifest=manifest,
        ),
        user_ids,
    )


def profile_action_for_rating(rating: float) -> SnapshotAction | None:
    if 3.5 <= rating <= 4.5:
        return SnapshotAction.PINNED
    if rating == 5.0:
        return SnapshotAction.SAVED
    return None


def positive_training_movie_ids(plan: dict) -> tuple[int, ...]:
    return tuple(
        int(item["movie_id"])
        for item in plan["train"]
        if profile_action_for_rating(float(item["rating"])) is not None
    )


def build_profile_signals(plan: dict) -> tuple[SnapshotSignal, ...]:
    user_id = int(plan["user_id"])
    as_of = datetime.fromisoformat(str(plan["evaluation_as_of"]))
    training_times = normalized_training_times(
        tuple(SimpleNamespace(**rating) for rating in plan["train"]),
        as_of=as_of,
    )
    signals = []
    for rating, occurred_at in zip(plan["train"], training_times, strict=True):
        action = profile_action_for_rating(float(rating["rating"]))
        if action is None:
            continue
        signals.append(
            SnapshotSignal(
                user_id=user_id,
                movie_id=int(rating["movie_id"]),
                action=action,
                occurred_at=occurred_at,
            )
        )
    return tuple(signals)


def profile_rows(
    features: tuple[ProfileFeatureSignal, ...],
) -> tuple[tuple[str, str, str, str, str, str, float], ...]:
    rows = []
    for signal in features:
        definition = get_feature_definition(signal.feature)
        if len(definition.ontology_relations) != 1 or definition.ontology_node_type is None:
            raise ValueError(
                f"profile feature has no single ontology relation: {signal.feature.value}"
            )
        rows.append(
            (
                definition.ontology_relations[0],
                signal.feature.value,
                definition.ontology_node_type,
                signal.ref_id,
                ProfileScope.LONG_TERM.value,
                FeatureDirection.POSITIVE.value,
                float(signal.score),
            )
        )
    return tuple(rows)


def build_ontology_contexts(
    plans: list[dict],
    *,
    artifact: OntologyArtifactView,
    edges_by_movie,
    profile_mode: str = "current",
    minimum_contribution_count: int = 1,
) -> dict[int, UserOntologyContext]:
    if minimum_contribution_count <= 0:
        raise ValueError("minimum contribution count must be positive")
    result = {}
    for index, plan in enumerate(plans, start=1):
        if index == 1 or index % 25 == 0 or index == len(plans):
            print(
                f"  ontology profiles ({profile_mode}): {index}/{len(plans)}",
                flush=True,
            )
        signals = build_profile_signals(plan)
        positive_movie_ids = {signal.movie_id for signal in signals}
        as_of = datetime.fromisoformat(str(plan["evaluation_as_of"]))
        features, _diagnostics = aggregate_profile_features(
            signals,
            edges_by_movie=edges_by_movie,
            ontology_build_id=artifact.ontology_build_id,
            direction=FeatureDirection.POSITIVE,
            as_of=as_of,
            decay=long_term_decay,
            top_k=PROFILE_FEATURE_TOP_K,
        )
        features = filter_profile_features_by_evidence(
            features,
            minimum_contribution_count=minimum_contribution_count,
        )
        candidate_ids = tuple(int(item["movie_id"]) for item in plan["holdout"])
        aggregate_rows = build_budgeted_candidate_aggregate_rows(
            artifact,  # type: ignore[arg-type]
            candidate_movie_ids=candidate_ids,
            profile_rows=profile_rows(features),
        )
        scores_by_movie = {movie_id: 0.0 for movie_id in candidate_ids}
        scores_by_family = {
            family: {movie_id: 0.0 for movie_id in candidate_ids}
            for family in ONTOLOGY_FAMILIES
        }
        family_totals: dict[str, float] = {}
        for movie_id, feature, _scope, _direction, raw_score, _peak, _count in aggregate_rows:
            family = str(feature)
            if family not in scores_by_family:
                raise ValueError(f"unexpected ontology family: {family}")
            normalized_movie_id = int(movie_id)
            normalized_score = float(raw_score)
            scores_by_movie[normalized_movie_id] += normalized_score
            scores_by_family[family][normalized_movie_id] += normalized_score
            family_totals[family] = family_totals.get(family, 0.0) + normalized_score
        source_user_id = int(plan["source_user_id"])
        result[source_user_id] = UserOntologyContext(
            source_user_id=source_user_id,
            scores_by_movie=scores_by_movie,
            profile_feature_count=len(features),
            positive_movie_count=len(positive_movie_ids),
            graph_covered_positive_movie_count=len(positive_movie_ids & edges_by_movie.keys()),
            matched_candidate_count=sum(score > 0.0 for score in scores_by_movie.values()),
            family_score_totals=family_totals,
            scores_by_family=scores_by_family,
        )
    return result


def evaluate_fusion_variants(
    model,
    plans: list[dict],
    *,
    cohort_name: str,
    seed: int,
    user_id_map: dict[int, int],
    movie_id_map: dict[int, int],
    user_features: csr_matrix,
    item_features: csr_matrix,
    ontology_contexts: dict[str, dict[int, UserOntologyContext]],
    variants: tuple[FusionVariant, ...] = BASE_FUSION_VARIANTS,
) -> list[dict]:
    rows = []
    for plan in plans:
        user_index = user_id_map[int(plan["user_id"])]
        user_biases, user_embeddings = model.get_user_representations(
            user_features[user_index]
        )
        candidate_ids = np.asarray(
            [int(item["movie_id"]) for item in plan["holdout"]], dtype=np.int64
        )
        item_indices = np.asarray(
            [movie_id_map[int(movie_id)] for movie_id in candidate_ids], dtype=np.int64
        )
        item_biases, item_embeddings = model.get_item_representations(
            item_features[item_indices]
        )
        model_scores = np.asarray(item_embeddings, dtype=np.float32) @ np.asarray(
            user_embeddings[0], dtype=np.float32
        )
        model_scores += float(user_biases[0])
        model_scores += np.asarray(item_biases, dtype=np.float32)
        normalized_model = percentile_normalize(
            dict(zip(map(int, candidate_ids), map(float, model_scores), strict=True))
        )
        source_user_id = int(plan["source_user_id"])
        relevance = {
            int(item["movie_id"]): rating_relevance(float(item["rating"]))
            for item in plan["holdout"]
        }
        candidate_count = len(candidate_ids)
        cutoff_20 = percentage_cutoff(candidate_count)
        for variant in variants:
            ontology_context = ontology_contexts[variant.profile_mode][source_user_id]
            raw_ontology_scores = ontology_scores_for_variant(
                ontology_context,
                excluded_families=variant.excluded_families,
            )
            ontology_scores = normalize_ontology_scores(
                raw_ontology_scores,
                method=variant.normalization,
            )
            correlation = (
                rank_correlation(normalized_model, ontology_scores)
                if variant.ontology_weight > 0.0
                else None
            )
            final_scores = {
                int(movie_id): variant.personal_weight
                * normalized_model[int(movie_id)]
                + variant.ontology_weight * ontology_scores[int(movie_id)]
                for movie_id in candidate_ids
            }
            ranking = rank_movies(final_scores)
            rows.append(
                {
                    "cohort": cohort_name,
                    "variant": variant.name,
                    "profile_mode": variant.profile_mode,
                    "ontology_normalization": variant.normalization,
                    "excluded_families": list(variant.excluded_families),
                    "seed": seed,
                    "source_user_id": source_user_id,
                    "profile_type": str(plan["profile_type"]),
                    "candidate_count": candidate_count,
                    "ndcg_at_100_percent": ndcg_at_k(
                        ranking, relevance, candidate_count
                    ),
                    "ndcg_at_20_percent": ndcg_at_k(ranking, relevance, cutoff_20),
                    "ontology_matched_candidate_count": sum(
                        score > 0.0 for score in raw_ontology_scores.values()
                    ),
                    "model_ontology_rank_correlation": correlation,
                }
            )
    return rows


def ontology_scores_for_variant(
    context: UserOntologyContext,
    *,
    excluded_families: tuple[str, ...],
) -> dict[int, float]:
    unknown_families = set(excluded_families) - set(ONTOLOGY_FAMILIES)
    if unknown_families:
        raise ValueError(
            f"unknown excluded ontology families: {sorted(unknown_families)}"
        )
    if not excluded_families:
        return dict(context.scores_by_movie)
    included_families = set(ONTOLOGY_FAMILIES) - set(excluded_families)
    return {
        movie_id: sum(
            context.scores_by_family[family][movie_id]
            for family in included_families
        )
        for movie_id in context.scores_by_movie
    }


def filter_profile_features_by_evidence(
    features: tuple[ProfileFeatureSignal, ...],
    *,
    minimum_contribution_count: int,
) -> tuple[ProfileFeatureSignal, ...]:
    if minimum_contribution_count <= 0:
        raise ValueError("minimum contribution count must be positive")
    return tuple(
        feature
        for feature in features
        if feature.contribution_count >= minimum_contribution_count
    )


def normalize_ontology_scores(
    scores_by_movie: dict[int, float],
    *,
    method: str,
) -> dict[int, float]:
    if method == "none":
        return {movie_id: 0.0 for movie_id in scores_by_movie}
    if method == "percentile":
        return percentile_normalize(scores_by_movie)
    if method != "bounded_raw":
        raise ValueError(f"unknown ontology normalization: {method}")
    if any(score < 0.0 or score > 1.000001 for score in scores_by_movie.values()):
        raise ValueError("field-budget ontology scores must remain between zero and one")
    return {
        int(movie_id): min(float(score), 1.0)
        for movie_id, score in scores_by_movie.items()
    }


def rank_movies(scores_by_movie: dict[int, float]) -> list[int]:
    return [
        movie_id
        for movie_id, _score in sorted(
            scores_by_movie.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def rank_correlation(
    model_scores: dict[int, float], ontology_scores: dict[int, float]
) -> float | None:
    movie_ids = sorted(model_scores)
    if movie_ids != sorted(ontology_scores):
        raise ValueError("model and ontology score movie IDs differ")
    model = np.asarray([model_scores[movie_id] for movie_id in movie_ids], dtype=np.float64)
    ontology = np.asarray(
        [ontology_scores[movie_id] for movie_id in movie_ids], dtype=np.float64
    )
    if np.ptp(model) == 0.0 or np.ptp(ontology) == 0.0:
        return None
    return float(np.corrcoef(model, ontology)[0, 1])


def build_concentration_diagnostic(
    rows: list[dict],
    *,
    cohorts: dict[str, list[dict]],
) -> dict:
    plan_by_user = {
        (cohort_name, int(plan["source_user_id"])): plan
        for cohort_name, plans in cohorts.items()
        for plan in plans
    }
    deltas = paired_variant_deltas(
        rows,
        baseline="model_only",
        changed=FAMILY_ABLATION_BASELINE.name,
        metric="ndcg_at_100_percent",
    )
    user_deltas: dict[tuple[str, int], list[float]] = {}
    for row in deltas:
        key = (str(row["cohort"]), int(row["source_user_id"]))
        user_deltas.setdefault(key, []).append(float(row["delta"]))

    users = []
    for key, values in sorted(user_deltas.items()):
        plan = plan_by_user[key]
        train_positive_count = sum(
            float(item["rating"]) >= 3.5 for item in plan["train"]
        )
        summary = plan["summary"]
        if train_positive_count != int(summary["train_positive_count"]):
            raise ValueError(
                f"train positive count differs from plan summary: {key}"
            )
        users.append(
            {
                "cohort": key[0],
                "source_user_id": key[1],
                "profile_type": str(plan["profile_type"]),
                "train_genre_entropy": float(summary["genre_entropy"]),
                "train_positive_count": train_positive_count,
                "mean_delta_ndcg_at_100_percent": statistics.mean(values),
            }
        )

    entropy_values = [float(row["train_genre_entropy"]) for row in users]
    evidence_values = [float(row["train_positive_count"]) for row in users]
    effect_values = [float(row["mean_delta_ndcg_at_100_percent"]) for row in users]
    cohort_correlations = {
        cohort_name: numeric_correlation(
            [
                float(row["train_genre_entropy"])
                for row in users
                if row["cohort"] == cohort_name
            ],
            [
                float(row["mean_delta_ndcg_at_100_percent"])
                for row in users
                if row["cohort"] == cohort_name
            ],
        )
        for cohort_name in cohorts
    }
    seed_correlations = {
        str(seed): numeric_correlation(
            [
                float(plan_by_user[(str(row["cohort"]), int(row["source_user_id"]))][
                    "summary"
                ]["genre_entropy"])
                for row in deltas
                if int(row["seed"]) == seed
            ],
            [
                float(row["delta"])
                for row in deltas
                if int(row["seed"]) == seed
            ],
        )
        for seed in sorted({int(row["seed"]) for row in deltas})
    }
    profile_types = sorted({str(row["profile_type"]) for row in users})
    profile_type_summaries = {
        profile_type: concentration_group_summary(
            [row for row in users if row["profile_type"] == profile_type]
        )
        for profile_type in profile_types
    }
    quartiles = concentration_quartiles(users)
    quartile_deltas = [
        float(quartile["mean_delta_ndcg_at_100_percent"]) for quartile in quartiles
    ]
    monotonic_decrease = all(
        left > right
        for left, right in zip(quartile_deltas, quartile_deltas[1:])
    )
    replicated_negative_correlation = all(
        value is not None and value < 0.0
        for value in (*cohort_correlations.values(), *seed_correlations.values())
    )
    low_high_direction_split = (
        quartile_deltas[0] > 0.0
        and quartile_deltas[1] > 0.0
        and quartile_deltas[2] < 0.0
        and quartile_deltas[3] < 0.0
    )
    directional_signal = (
        monotonic_decrease
        and replicated_negative_correlation
        and low_high_direction_split
    )
    focused_entropies = [
        float(row["train_genre_entropy"])
        for row in users
        if row["profile_type"] == "stable_focused"
    ]
    diverse_entropies = [
        float(row["train_genre_entropy"])
        for row in users
        if row["profile_type"] == "stable_diverse"
    ]
    tail_selected = bool(focused_entropies and diverse_entropies)
    continuous_profile_types = {
        "stable_low_entropy",
        "stable_mid_entropy",
        "stable_high_entropy",
    }
    continuous_population = continuous_profile_types <= set(profile_types)
    if tail_selected:
        selection_gap = {
            "maximum_focused_entropy": max(focused_entropies),
            "minimum_diverse_entropy": min(diverse_entropies),
            "gap": min(diverse_entropies) - max(focused_entropies),
        }
    else:
        selection_gap = None
    if directional_signal and continuous_population:
        verdict = "directional_signal_ready_for_conditional_fusion_experiment"
    elif directional_signal:
        verdict = "directional_signal_requires_middle_population_calibration"
    else:
        verdict = "concentration_does_not_explain_ontology_effect"
    return {
        "metric": {
            "name": "normalized train-positive genre entropy",
            "source": "70% training window positive movies and their catalog genres",
            "serving_inputs_available": True,
            "holdout_used": False,
            "lower_means_more_concentrated": True,
        },
        "user_count": len(users),
        "pearson_entropy_vs_ontology_delta": numeric_correlation(
            entropy_values, effect_values
        ),
        "spearman_entropy_vs_ontology_delta": spearman_correlation(
            entropy_values, effect_values
        ),
        "pearson_positive_count_vs_ontology_delta": numeric_correlation(
            evidence_values, effect_values
        ),
        "cohort_pearson_correlations": cohort_correlations,
        "seed_pearson_correlations": seed_correlations,
        "profile_type_summaries": profile_type_summaries,
        "entropy_quartiles": quartiles,
        "sampling_design": {
            "tail_selected": tail_selected,
            "continuous_low_middle_high": continuous_population,
        },
        "selection_gap": selection_gap,
        "checks": {
            "quartile_effect_monotonically_decreases": monotonic_decrease,
            "negative_correlation_in_every_cohort_and_seed": replicated_negative_correlation,
            "low_two_positive_high_two_negative": low_high_direction_split,
        },
        "directional_signal_found": directional_signal,
        "serving_threshold_selected": False,
        "verdict": verdict,
        "users": users,
    }


def concentration_group_summary(rows: list[dict]) -> dict:
    return {
        "user_count": len(rows),
        "mean_train_genre_entropy": statistics.mean(
            float(row["train_genre_entropy"]) for row in rows
        ),
        "minimum_train_genre_entropy": min(
            float(row["train_genre_entropy"]) for row in rows
        ),
        "maximum_train_genre_entropy": max(
            float(row["train_genre_entropy"]) for row in rows
        ),
        "mean_train_positive_count": statistics.mean(
            float(row["train_positive_count"]) for row in rows
        ),
        "mean_delta_ndcg_at_100_percent": statistics.mean(
            float(row["mean_delta_ndcg_at_100_percent"]) for row in rows
        ),
        "user_direction_counts": {
            "improved": sum(
                float(row["mean_delta_ndcg_at_100_percent"]) > 0.0 for row in rows
            ),
            "declined": sum(
                float(row["mean_delta_ndcg_at_100_percent"]) < 0.0 for row in rows
            ),
            "equal": sum(
                float(row["mean_delta_ndcg_at_100_percent"]) == 0.0 for row in rows
            ),
        },
    }


def concentration_quartiles(users: list[dict]) -> list[dict]:
    if len(users) < 4:
        raise ValueError("concentration diagnostic requires at least four users")
    ordered = sorted(
        users,
        key=lambda row: (
            float(row["train_genre_entropy"]),
            str(row["cohort"]),
            int(row["source_user_id"]),
        ),
    )
    groups = np.array_split(np.asarray(ordered, dtype=object), 4)
    return [
        {
            "quartile": index,
            **concentration_group_summary(list(group)),
        }
        for index, group in enumerate(groups, start=1)
    ]


def numeric_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or not left:
        raise ValueError("correlation inputs must have the same non-zero length")
    left_values = np.asarray(left, dtype=np.float64)
    right_values = np.asarray(right, dtype=np.float64)
    if np.ptp(left_values) == 0.0 or np.ptp(right_values) == 0.0:
        return None
    return float(np.corrcoef(left_values, right_values)[0, 1])


def spearman_correlation(left: list[float], right: list[float]) -> float | None:
    return numeric_correlation(average_ranks(left), average_ranks(right))


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + end - 1) / 2.0
        for position in range(start, end):
            ranks[order[position]] = average_rank
        start = end
    return ranks


def build_report(
    rows: list[dict],
    *,
    cohorts: dict[str, list[dict]],
    ontology_contexts: dict[str, dict[str, dict[int, UserOntologyContext]]],
    training_seconds: dict[str, list[float]],
    seeds: tuple[int, ...],
    source_artifact: Path,
    artifact: OntologyArtifactView,
    variants: tuple[FusionVariant, ...],
    case_id: str,
    development_paths: dict[str, Path],
    experiment_mode: str = "baseline",
) -> dict:
    development_user_counts = {
        cohort_name: len(plans) for cohort_name, plans in cohorts.items()
    }
    summaries = []
    for cohort_name in cohorts:
        for variant in variants:
            selected = [
                row
                for row in rows
                if row["cohort"] == cohort_name and row["variant"] == variant.name
            ]
            summaries.append(
                {
                    "cohort": cohort_name,
                    "variant": variant.name,
                    "profile_mode": variant.profile_mode,
                    "ontology_normalization": variant.normalization,
                    "personal_weight": variant.personal_weight,
                    "ontology_weight": variant.ontology_weight,
                    "excluded_families": list(variant.excluded_families),
                    "ndcg_at_100_percent": statistics.mean(
                        float(row["ndcg_at_100_percent"]) for row in selected
                    ),
                    "ndcg_at_20_percent": statistics.mean(
                        float(row["ndcg_at_20_percent"]) for row in selected
                    ),
                    "mean_training_seconds": statistics.mean(
                        training_seconds[cohort_name]
                    ),
                }
            )
    available_variants = {variant.name for variant in variants}
    primary_changed_variant = (
        "current_75_25"
        if "current_75_25" in available_variants
        else FAMILY_ABLATION_BASELINE.name
    )
    current_effect = comparison_effect(
        rows,
        baseline="model_only",
        changed=primary_changed_variant,
        metric="ndcg_at_100_percent",
    )
    current_effect_20 = comparison_effect(
        rows,
        baseline="model_only",
        changed=primary_changed_variant,
        metric="ndcg_at_20_percent",
    )
    current_effect_20["mean_delta_ndcg_at_20_percent"] = current_effect_20.pop(
        "mean_delta_ndcg_at_100_percent"
    )
    diagnostic_comparisons = (
        (
            "bounded_current_profile_vs_model",
            "model_only",
            "current_bounded_75_25",
        ),
        (
            "normalization_effect_current_profile",
            "current_75_25",
            "current_bounded_75_25",
        ),
        (
            "profile_breadth_effect_percentile",
            "current_75_25",
            "repeated_evidence_percentile_75_25",
        ),
        (
            "normalization_effect_repeated_profile",
            "repeated_evidence_percentile_75_25",
            "repeated_evidence_bounded_75_25",
        ),
        (
            "profile_breadth_effect_bounded",
            "current_bounded_75_25",
            "repeated_evidence_bounded_75_25",
        ),
        (
            "combined_repair_vs_model",
            "model_only",
            "repeated_evidence_bounded_75_25",
        ),
    )
    diagnostic_effects = {
        name: comparison_effect(
            rows,
            baseline=baseline,
            changed=changed,
            metric="ndcg_at_100_percent",
        )
        for name, baseline, changed in diagnostic_comparisons
        if {baseline, changed} <= available_variants
    }
    family_ablation_effects = {
        family: comparison_effect(
            rows,
            baseline=FAMILY_ABLATION_BASELINE.name,
            changed=f"current_bounded_without_{family}",
            metric="ndcg_at_100_percent",
        )
        for family in ONTOLOGY_FAMILIES
        if f"current_bounded_without_{family}" in available_variants
    }
    concentration_diagnostic = (
        build_concentration_diagnostic(rows, cohorts=cohorts)
        if experiment_mode == "concentration_diagnostic"
        else None
    )
    correlation_variant_by_profile: dict[str, str] = {}
    for variant in variants:
        if (
            variant.ontology_weight > 0.0
            and not variant.excluded_families
            and variant.profile_mode not in correlation_variant_by_profile
        ):
            correlation_variant_by_profile[variant.profile_mode] = variant.name
    coverage = {
        cohort_name: {
            profile_mode: ontology_coverage_summary(
                contexts,
                model_ontology_correlations=[
                    float(row["model_ontology_rank_correlation"])
                    for row in rows
                    if row["cohort"] == cohort_name
                    and row["profile_mode"] == profile_mode
                    and row["variant"]
                    == correlation_variant_by_profile.get(profile_mode)
                    and row["model_ontology_rank_correlation"] is not None
                ],
            )
            for profile_mode, contexts in profile_contexts.items()
        }
        for cohort_name, profile_contexts in ontology_contexts.items()
    }
    is_diagnostic = bool(
        diagnostic_effects or family_ablation_effects or concentration_diagnostic
    )
    current_cohort_deltas = current_effect["cohort_deltas"]
    return {
        "case_id": case_id,
        "scope": (
            "train-only genre concentration and bounded ontology effect diagnostic"
            if concentration_diagnostic
            else "bounded ontology leave-one-family-out diagnostic"
            if family_ablation_effects
            else "ontology profile breadth and score normalization diagnostic"
            if diagnostic_effects
            else "final long-term ontology score fusion on fixed holdout movies"
        ),
        "controls": {
            "development_user_count_per_cohort": (
                next(iter(development_user_counts.values()))
                if len(set(development_user_counts.values())) == 1
                else None
            ),
            "development_user_counts": development_user_counts,
            "candidate_set": "each user's temporal 30% holdout only",
            "primary_metric": "NDCG@100%",
            "secondary_metric": "NDCG@20%",
            "seeds": list(seeds),
            "development_plans": {
                name: {
                    "path": str(path),
                    "sha256": file_sha256(path),
                }
                for name, path in development_paths.items()
            },
            "source_artifact": str(source_artifact),
            "source_model_build_id": str(artifact.manifest["model_build_id"]),
            "source_ontology_build_id": artifact.ontology_build_id,
            "lightfm_core": {
                "signal": "v3",
                "identity": "supported",
                "config": "v3",
            },
            "production_candidate_retrieval_included": False,
            "hard_filters_and_other_policy_adjustments_included": False,
            "active_bundle_changed": False,
            "models_persisted": False,
            "experiment_mode": experiment_mode,
            "diagnostic_grid": experiment_mode == "profile_normalization_grid",
            "family_ablation": experiment_mode == "family_ablation",
            "family_ablation_budget_redistributed": False,
            "concentration_diagnostic": experiment_mode
            == "concentration_diagnostic",
            "concentration_uses_holdout_labels": False,
            "repeated_evidence_minimum_contribution_count": 2,
        },
        "variant_summaries": summaries,
        "paired_effect_ndcg_at_100_percent": current_effect,
        "paired_effect_ndcg_at_20_percent": current_effect_20,
        "diagnostic_effects_ndcg_at_100_percent": diagnostic_effects,
        "family_ablation_effects_ndcg_at_100_percent": family_ablation_effects,
        "concentration_diagnostic": concentration_diagnostic,
        "ontology_coverage": coverage,
        "gate": {
            "applicable": not is_diagnostic,
            "passed": (
                current_effect["verdict"] == "consistent_gain"
                and all(delta > 0.0 for delta in current_cohort_deltas.values())
                if not is_diagnostic
                else None
            ),
            "require_positive_bootstrap_interval": True,
            "require_all_seed_deltas_positive": True,
            "require_all_profile_type_deltas_positive": True,
            "require_both_development_cohort_deltas_positive": True,
        },
        "rows": rows,
    }


def comparison_effect(
    rows: list[dict],
    *,
    baseline: str,
    changed: str,
    metric: str,
) -> dict:
    deltas = paired_variant_deltas(
        rows,
        baseline=baseline,
        changed=changed,
        metric=metric,
    )
    effect = summarize_deltas(deltas, baseline=baseline, changed=changed)
    effect["cohort_deltas"] = grouped_delta_means(deltas, "cohort")
    return effect


def paired_variant_deltas(
    rows: list[dict],
    *,
    baseline: str,
    changed: str,
    metric: str,
) -> list[dict]:
    indexed = {
        (
            str(row["cohort"]),
            int(row["seed"]),
            int(row["source_user_id"]),
            str(row["variant"]),
        ): float(row[metric])
        for row in rows
    }
    result = []
    observations = sorted(
        {
            (str(row["cohort"]), int(row["seed"]), int(row["source_user_id"]))
            for row in rows
        }
    )
    for cohort, seed, source_user_id in observations:
        template = next(
            row
            for row in rows
            if row["cohort"] == cohort
            and int(row["seed"]) == seed
            and int(row["source_user_id"]) == source_user_id
        )
        result.append(
            {
                "cohort": cohort,
                "seed": seed,
                "source_user_id": source_user_id,
                "profile_type": str(template["profile_type"]),
                "delta": indexed[(cohort, seed, source_user_id, changed)]
                - indexed[(cohort, seed, source_user_id, baseline)],
            }
        )
    return result


def paired_deltas(rows: list[dict], *, metric: str) -> list[dict]:
    return paired_variant_deltas(
        rows,
        baseline="model_only",
        changed="current_75_25",
        metric=metric,
    )


def grouped_delta_means(rows: list[dict], key: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(float(row["delta"]))
    return {
        name: statistics.mean(values) for name, values in sorted(grouped.items())
    }


def ontology_coverage_summary(
    contexts: dict[int, UserOntologyContext],
    *,
    model_ontology_correlations: list[float],
) -> dict:
    values = list(contexts.values())
    family_totals: dict[str, float] = {}
    for context in values:
        for family, score in context.family_score_totals.items():
            family_totals[family] = family_totals.get(family, 0.0) + score
    total_family_score = sum(family_totals.values())
    return {
        "user_count": len(values),
        "mean_profile_feature_count": statistics.mean(
            value.profile_feature_count for value in values
        ),
        "mean_graph_positive_movie_coverage": statistics.mean(
            value.graph_covered_positive_movie_count / value.positive_movie_count
            if value.positive_movie_count
            else 0.0
            for value in values
        ),
        "mean_candidate_match_rate": statistics.mean(
            value.matched_candidate_count / len(value.scores_by_movie)
            if value.scores_by_movie
            else 0.0
            for value in values
        ),
        "zero_match_user_count": sum(
            value.matched_candidate_count == 0 for value in values
        ),
        "mean_raw_score_range": statistics.mean(
            max(value.scores_by_movie.values(), default=0.0)
            - min(value.scores_by_movie.values(), default=0.0)
            for value in values
        ),
        "mean_model_ontology_rank_correlation": (
            statistics.mean(model_ontology_correlations)
            if model_ontology_correlations
            else None
        ),
        "family_score_share": {
            family: score / total_family_score if total_family_score else 0.0
            for family, score in sorted(family_totals.items())
        },
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def report_markdown(report: dict) -> str:
    effect = report["paired_effect_ndcg_at_100_percent"]
    interval = effect["paired_user_bootstrap_95_percent_ci"]
    cohort_counts = ", ".join(
        f"{name}={count}"
        for name, count in report["controls"]["development_user_counts"].items()
    )
    lines = [
        f"# {report['case_id']} Ontology Fusion Result",
        "",
        "## Controls",
        "",
        f"- Development cohorts: `{cohort_counts}`",
        f"- Seeds: `{report['controls']['seeds']}`",
        "- Candidate set: each user's temporal 30% holdout only",
        "- LightFM core: locked V3 core",
        "- LightFM and ontology component weights: fixed",
        "- Candidate retrieval, hard filters, quality, OTT and MMR: excluded",
        "- Active model and bundle: unchanged",
        "",
        "## Scores",
        "",
        "| Cohort | Variant | Profile | Normalization | Excluded families | NDCG@100% | NDCG@20% |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in report["variant_summaries"]:
        lines.append(
            f"| {row['cohort']} | {row['variant']} | "
            f"{row['profile_mode']} | {row['ontology_normalization']} | "
            f"{', '.join(row['excluded_families']) or '-'} | "
            f"{row['ndcg_at_100_percent']:.6f} | "
            f"{row['ndcg_at_20_percent']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Paired Effect",
            "",
            f"- NDCG@100% delta: `{effect['mean_delta_ndcg_at_100_percent']:+.6f}`",
            f"- Paired-user bootstrap 95% CI: `[{interval['lower']:+.6f}, {interval['upper']:+.6f}]`",
            f"- Seed deltas: `{effect['seed_deltas']}`",
            f"- Profile-type deltas: `{effect['segment_deltas']}`",
            f"- Cohort deltas: `{effect['cohort_deltas']}`",
            f"- Verdict: `{effect['verdict']}`",
            f"- Gate applicable: `{report['gate']['applicable']}`",
            f"- Gate passed: `{report['gate']['passed']}`",
        ]
    )
    if report["diagnostic_effects_ndcg_at_100_percent"]:
        lines.extend(
            [
                "",
                "## Diagnostic Effects",
                "",
                "| Comparison | NDCG@100% delta | 95% CI | Cohort deltas | Verdict |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for name, diagnostic in report[
            "diagnostic_effects_ndcg_at_100_percent"
        ].items():
            diagnostic_interval = diagnostic["paired_user_bootstrap_95_percent_ci"]
            lines.append(
                f"| {name} | "
                f"{diagnostic['mean_delta_ndcg_at_100_percent']:+.6f} | "
                f"[{diagnostic_interval['lower']:+.6f}, "
                f"{diagnostic_interval['upper']:+.6f}] | "
                f"`{diagnostic['cohort_deltas']}` | {diagnostic['verdict']} |"
            )
    if report["family_ablation_effects_ndcg_at_100_percent"]:
        lines.extend(
            [
                "",
                "## Family Ablation Effects",
                "",
                "Positive delta means that removing the family improved ranking. "
                "Negative delta means that removing it hurt ranking.",
                "",
                "| Removed family | Without - full delta | 95% CI | Seed deltas | Profile-type deltas | Cohort deltas | Interpretation |",
                "| --- | ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for family, diagnostic in report[
            "family_ablation_effects_ndcg_at_100_percent"
        ].items():
            diagnostic_interval = diagnostic["paired_user_bootstrap_95_percent_ci"]
            interpretation = {
                "consistent_gain": "harmful in current fusion",
                "consistent_loss": "useful in current fusion",
                "uncertain": "uncertain",
            }[diagnostic["verdict"]]
            lines.append(
                f"| {family} | "
                f"{diagnostic['mean_delta_ndcg_at_100_percent']:+.6f} | "
                f"[{diagnostic_interval['lower']:+.6f}, "
                f"{diagnostic_interval['upper']:+.6f}] | "
                f"`{diagnostic['seed_deltas']}` | "
                f"`{diagnostic['segment_deltas']}` | "
                f"`{diagnostic['cohort_deltas']}` | {interpretation} |"
            )
    concentration = report["concentration_diagnostic"]
    if concentration:
        lines.extend(
            [
                "",
                "## Concentration Diagnostic",
                "",
                "The concentration metric uses only positive movies from the 70% training window. Holdout ratings are not used to compute it.",
                "",
                f"- Entropy/ontology-effect Pearson correlation: `{concentration['pearson_entropy_vs_ontology_delta']:+.6f}`",
                f"- Entropy/ontology-effect Spearman correlation: `{concentration['spearman_entropy_vs_ontology_delta']:+.6f}`",
                f"- Positive-count/effect Pearson correlation: `{concentration['pearson_positive_count_vs_ontology_delta']:+.6f}`",
                f"- Cohort correlations: `{concentration['cohort_pearson_correlations']}`",
                f"- Seed correlations: `{concentration['seed_pearson_correlations']}`",
                f"- Directional signal found: `{concentration['directional_signal_found']}`",
                f"- Serving threshold selected: `{concentration['serving_threshold_selected']}`",
                f"- Verdict: `{concentration['verdict']}`",
                "",
                "| Entropy quartile | Users | Entropy range | Mean ontology delta | Improved | Declined |",
                "| --- | ---: | --- | ---: | ---: | ---: |",
            ]
        )
        for quartile in concentration["entropy_quartiles"]:
            counts = quartile["user_direction_counts"]
            lines.append(
                f"| Q{quartile['quartile']} | {quartile['user_count']} | "
                f"{quartile['minimum_train_genre_entropy']:.4f}~"
                f"{quartile['maximum_train_genre_entropy']:.4f} | "
                f"{quartile['mean_delta_ndcg_at_100_percent']:+.6f} | "
                f"{counts['improved']} | {counts['declined']} |"
            )
        lines.extend(
            [
                "",
                "| Profile bin | Users | Mean entropy | Mean ontology delta | Improved | Declined |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for profile_type, profile in concentration["profile_type_summaries"].items():
            counts = profile["user_direction_counts"]
            lines.append(
                f"| {profile_type} | {profile['user_count']} | "
                f"{profile['mean_train_genre_entropy']:.6f} | "
                f"{profile['mean_delta_ndcg_at_100_percent']:+.6f} | "
                f"{counts['improved']} | {counts['declined']} |"
            )
        sampling = concentration["sampling_design"]
        if sampling["tail_selected"]:
            threshold_note = (
                "No serving threshold is selected because these cohorts contain "
                "deliberately sampled low- and high-entropy users, not the middle "
                "of the population."
            )
        elif concentration["directional_signal_found"]:
            threshold_note = (
                "No serving threshold is selected in this diagnostic; a conditional "
                "fusion formula must be compared separately."
            )
        else:
            threshold_note = (
                "No serving threshold is selected because the continuous cohorts did "
                "not reproduce an ontology-loss transition by entropy."
            )
        lines.extend(
            [
                "",
                threshold_note,
            ]
        )
    lines.extend(
        [
            "",
            "## Ontology Coverage",
            "",
            "| Cohort | Profile | Graph positive coverage | Candidate match rate | Model/ontology correlation | Mean raw range | Mean profile features |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for cohort, profile_coverages in report["ontology_coverage"].items():
        for profile_mode, coverage in profile_coverages.items():
            correlation = coverage["mean_model_ontology_rank_correlation"]
            correlation_text = f"{correlation:.4f}" if correlation is not None else "n/a"
            lines.append(
                f"| {cohort} | {profile_mode} | "
                f"{coverage['mean_graph_positive_movie_coverage']:.4f} | "
                f"{coverage['mean_candidate_match_rate']:.4f} | "
                f"{correlation_text} | "
                f"{coverage['mean_raw_score_range']:.4f} | "
                f"{coverage['mean_profile_feature_count']:.2f} |"
            )
    lines.extend(
        [
            "",
            "This is a final-score component diagnostic, not a full-catalog retrieval test.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
