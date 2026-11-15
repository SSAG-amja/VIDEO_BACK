from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from app.core.redis import get_redis
from app.db.session import SessionLocal
from app.jobs.recsys.v3.diagnostics.quality_snapshot import (
    DEFAULT_OUTPUT_DIR,
    genre_alignment,
    load_movie_metadata,
    load_post_model_users,
    load_representative_users,
    top_genre_ids,
)
from app.services.recsys.v3.config import (
    POLICY_NEGATIVE_FEATURE_WEIGHTS,
    POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER,
)
from app.services.recsys.v3.policy.policy_engine import evaluate_policy_candidates
from app.services.recsys.v3.policy.policy_schemas import PolicyAdjustmentSettings
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.recommender import _load_published_candidates, _policy_context
from app.services.recsys.v3.retrieval.lightfm_retriever import retrieve_lightfm_candidates
from app.services.recsys.v3.retrieval.retrieval_pipeline import build_retrieval_candidates
from app.services.recsys.v3.serving.serving_bundle import get_active_serving_bundle


# Preserve the pre-Phase-F baseline so rerunning this diagnostic remains comparable.
CURRENT = PolicyAdjustmentSettings(catalog_trust_penalty_max=0.0)
CATALOG_SOFT = PolicyAdjustmentSettings(catalog_trust_penalty_max=0.05)
NEGATIVE_DISABLED = PolicyAdjustmentSettings(
    catalog_trust_penalty_max=0.0,
    negative_max_base_ratio=0.0,
    negative_max_absolute=0.0,
)
VARIANTS = {
    "current": CURRENT,
    "catalog_soft": CATALOG_SOFT,
    "negative_disabled": NEGATIVE_DISABLED,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare V3 catalog-trust and semantic-negative policy variants."
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    if args.limit <= 0 or args.limit > 100:
        raise ValueError("Phase F diagnostic limit must be between 1 and 100")

    bundle = get_active_serving_bundle()
    redis = get_redis()
    with SessionLocal() as db:
        users = load_post_model_users(db)
        users.extend(
            user
            for user in load_representative_users(db)
            if user["profile_type"] == "negative_heavy"
        )
        rows = [
            analyze_user(
                db,
                redis=redis,
                bundle=bundle,
                user=user,
                limit=args.limit,
            )
            for user in users
        ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "user_count": len(rows),
            "limit": args.limit,
            "bundle_id": bundle.bundle_id,
            "model_build_id": bundle.model.model_build_id,
            "ontology_build_id": bundle.ontology_build_id,
            "candidate_snapshot_id": bundle.candidate_snapshot_id,
            "variants": {
                name: adjustment_dict(settings)
                for name, settings in VARIANTS.items()
            },
            "method": (
                "Candidate retrieval and ontology analysis run once per user; only policy "
                "adjustments differ across variants."
            ),
        },
        "summary": summarize(rows),
        "users": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_catalog_negative_ablation_{timestamp}.json"
    markdown_path = args.output_dir / f"v3_catalog_negative_ablation_{timestamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(markdown_path),
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )


def analyze_user(db, *, redis, bundle, user: dict, limit: int) -> dict:
    model_user_known = bundle.model.user_index(user["user_id"]) is not None
    profile = build_user_runtime_profile(
        db,
        user_id=user["user_id"],
        ontology_build_id=bundle.ontology_build_id,
        as_of=datetime.now(timezone.utc),
        model_user_known=model_user_known,
    ).bundle
    published, _kind = _load_published_candidates(db, bundle=bundle, profile=profile)
    excluded = profile.long_term.excluded_movie_ids | profile.short_term.recent_negative_movie_ids
    long_term = published or retrieve_lightfm_candidates(
        bundle.model,
        profile=profile,
        excluded_movie_ids=excluded,
    )
    context = _policy_context(redis, user["user_id"], profile)
    retrieval = build_retrieval_candidates(
        db,
        ontology_build_id=bundle.ontology_build_id,
        profile=profile,
        long_term_candidates=long_term,
        context=context,
        redis=redis,
    )
    results = {
        name: evaluate_policy_candidates(
            db,
            retrieval=retrieval,
            profile=profile,
            context=context,
            adjustment_settings=settings,
        )
        for name, settings in VARIANTS.items()
    }
    movie_ids = {
        item.movie_id
        for result in results.values()
        for item in result.candidates[:limit]
    }
    metadata = load_movie_metadata(db, movie_ids)
    long_genres = top_genre_ids(profile.long_term.positive_features)
    short_genres = top_genre_ids(profile.short_term.positive_features)
    excluded_ids = (
        profile.long_term.excluded_movie_ids
        | profile.short_term.recent_negative_movie_ids
    )
    metrics = {
        name: variant_metrics(
            result.candidates,
            metadata=metadata,
            long_genres=long_genres,
            short_genres=short_genres,
            excluded_ids=excluded_ids,
            limit=limit,
        )
        for name, result in results.items()
    }
    current_ids = {
        item.movie_id for item in results["current"].candidates[:limit]
    }
    for name, result in results.items():
        selected_ids = {item.movie_id for item in result.candidates[:limit]}
        metrics[name]["current_overlap_ratio"] = round(
            len(current_ids & selected_ids) / limit,
            6,
        )
    return {
        **user,
        "preference_state": profile.short_term.preference_state.value,
        "passed_pair_count": profile.long_term.passed_pair_count,
        "variants": metrics,
    }


def variant_metrics(
    candidates,
    *,
    metadata: dict[int, dict],
    long_genres: set[int],
    short_genres: set[int],
    excluded_ids: frozenset[int],
    limit: int,
) -> dict:
    selected = candidates[:limit]
    rows = [
        {
            "movie_id": item.movie_id,
            "genre_ids": metadata.get(item.movie_id, {}).get("genre_ids", []),
        }
        for item in selected
    ]
    vote_counts = [int(metadata.get(item.movie_id, {}).get("vote_count") or 0) for item in selected]
    negative_evidence = [weighted_negative_evidence(item) for item in selected]
    return {
        "candidate_count": len(selected),
        "zero_vote_ratio": ratio(sum(value == 0 for value in vote_counts), len(selected)),
        "low_vote_ratio": ratio(sum(0 < value < 20 for value in vote_counts), len(selected)),
        "trusted_vote_ratio": ratio(sum(value >= 20 for value in vote_counts), len(selected)),
        "long_genre_share": genre_alignment(rows, long_genres)["mean_genre_share"],
        "short_genre_share": genre_alignment(rows, short_genres)["mean_genre_share"],
        "weighted_negative_evidence_mean": rounded_mean(negative_evidence),
        "semantic_negative_penalty_mean": rounded_mean(
            item.score.negative_preference_penalty for item in selected
        ),
        "catalog_trust_penalty_mean": rounded_mean(
            item.score.catalog_trust_penalty for item in selected
        ),
        "exact_exclusion_violations": sum(item.movie_id in excluded_ids for item in selected),
        "movie_ids": [item.movie_id for item in selected],
    }


def weighted_negative_evidence(candidate) -> float:
    return sum(
        POLICY_NEGATIVE_FEATURE_WEIGHTS[type_score.feature.value]
        * (
            type_score.long_negative_score
            + POLICY_NEGATIVE_SHORT_TERM_MULTIPLIER * type_score.short_negative_score
        )
        for type_score in candidate.ontology.type_scores
    )


def summarize(rows: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["profile_type"]].append(row)
    return {
        profile_type: {
            "user_count": len(values),
            "variants": {
                variant: aggregate_variant(values, variant=variant)
                for variant in VARIANTS
            },
        }
        for profile_type, values in by_type.items()
    }


def aggregate_variant(rows: list[dict], *, variant: str) -> dict:
    keys = (
        "zero_vote_ratio",
        "low_vote_ratio",
        "trusted_vote_ratio",
        "long_genre_share",
        "short_genre_share",
        "weighted_negative_evidence_mean",
        "semantic_negative_penalty_mean",
        "catalog_trust_penalty_mean",
        "current_overlap_ratio",
    )
    result = {
        key: rounded_mean(row["variants"][variant][key] for row in rows)
        for key in keys
    }
    result["exact_exclusion_violations"] = sum(
        row["variants"][variant]["exact_exclusion_violations"] for row in rows
    )
    return result


def adjustment_dict(settings: PolicyAdjustmentSettings) -> dict:
    return {
        "catalog_trust_penalty_max": settings.catalog_trust_penalty_max,
        "catalog_trust_vote_threshold": settings.catalog_trust_vote_threshold,
        "negative_max_base_ratio": settings.negative_max_base_ratio,
        "negative_max_absolute": settings.negative_max_absolute,
    }


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def rounded_mean(values) -> float:
    materialized = list(values)
    return round(mean(materialized), 6) if materialized else 0.0


def render_markdown(report: dict) -> str:
    lines = [
        "# V3 Phase F Catalog And Negative Policy Ablation",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- users: `{report['scope']['user_count']}`",
        f"- bundle: `{report['scope']['bundle_id']}`",
        "- method: each user reuses one retrieval/ontology result across all policy variants",
        "",
        "## Results",
        "",
        "| profile | variant | vote=0 | vote 1-19 | vote>=20 | long genre | short genre | negative evidence | negative penalty | overlap | violations |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile_type, profile in report["summary"].items():
        for variant, values in profile["variants"].items():
            lines.append(
                "| "
                + " | ".join(
                    (
                        profile_type,
                        variant,
                        f"{values['zero_vote_ratio']:.3f}",
                        f"{values['low_vote_ratio']:.3f}",
                        f"{values['trusted_vote_ratio']:.3f}",
                        f"{values['long_genre_share']:.3f}",
                        f"{values['short_genre_share']:.3f}",
                        f"{values['weighted_negative_evidence_mean']:.3f}",
                        f"{values['semantic_negative_penalty_mean']:.3f}",
                        f"{values['current_overlap_ratio']:.3f}",
                        str(values["exact_exclusion_violations"]),
                    )
                )
                + " |"
            )
    lines.extend(
        (
            "",
            "## Interpretation Rules",
            "",
            "- Catalog soft penalty is acceptable only when low-evidence exposure falls without a material genre-alignment loss.",
            "- Current semantic-negative penalty is useful when its selected weighted-negative evidence is lower than the disabled variant.",
            "- Exact passed/recent-negative exclusions must remain zero for every variant.",
            "",
        )
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
