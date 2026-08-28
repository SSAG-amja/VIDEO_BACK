from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from app.core.redis import get_redis
from app.db.session import SessionLocal
from app.jobs.recsys.v3.diagnostics.quality_snapshot import (
    DEFAULT_OUTPUT_DIR,
    POST_MODEL_PROFILE_TYPES,
    genre_alignment,
    load_movie_metadata,
    load_post_model_users,
    top_genre_ids,
)
from app.services.recsys.v3.policy.policy_engine import evaluate_policy_candidates
from app.services.recsys.v3.policy.policy_schemas import PolicyComponentWeights
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.recommender import _load_published_candidates, _policy_context
from app.services.recsys.v3.retrieval.lightfm_retriever import retrieve_lightfm_candidates
from app.services.recsys.v3.retrieval.retrieval_pipeline import build_retrieval_candidates
from app.services.recsys.v3.retrieval.retrieval_schemas import CandidateSource
from app.services.recsys.v3.serving.serving_bundle import get_active_serving_bundle


CURRENT_WEIGHTS = PolicyComponentWeights()
NO_ONTOLOGY_WEIGHTS = PolicyComponentWeights(personal=1.0, ontology=0.0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare fixed V3 candidates with and without the policy ontology component."
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    if args.limit <= 0 or args.limit > 100:
        raise ValueError("ontology ablation limit must be between 1 and 100")

    bundle = get_active_serving_bundle()
    redis = get_redis()
    with SessionLocal() as db:
        users = load_post_model_users(db)
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
            "scenario": "post-model",
            "limit": args.limit,
            "bundle_id": bundle.bundle_id,
            "model_build_id": bundle.model.model_build_id,
            "ontology_build_id": bundle.ontology_build_id,
            "candidate_snapshot_id": bundle.candidate_snapshot_id,
            "current_weights": weight_dict(CURRENT_WEIGHTS),
            "no_ontology_weights": weight_dict(NO_ONTOLOGY_WEIGHTS),
        },
        "summary": summarize(rows),
        "users": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_ontology_ablation_{timestamp}.json"
    markdown_path = args.output_dir / f"v3_ontology_ablation_{timestamp}.md"
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
    current = evaluate_policy_candidates(
        db,
        retrieval=retrieval,
        profile=profile,
        context=context,
        component_weights=CURRENT_WEIGHTS,
    )
    no_ontology = evaluate_policy_candidates(
        db,
        retrieval=retrieval,
        profile=profile,
        context=context,
        component_weights=NO_ONTOLOGY_WEIGHTS,
    )
    movie_ids = {
        item.movie_id
        for result in (current, no_ontology)
        for item in result.candidates[:limit]
    }
    metadata = load_movie_metadata(db, movie_ids)
    long_genres = top_genre_ids(profile.long_term.positive_features)
    short_genres = top_genre_ids(profile.short_term.positive_features)
    current_metrics = policy_metrics(
        current.candidates,
        metadata=metadata,
        long_genres=long_genres,
        short_genres=short_genres,
        limit=limit,
    )
    no_ontology_metrics = policy_metrics(
        no_ontology.candidates,
        metadata=metadata,
        long_genres=long_genres,
        short_genres=short_genres,
        limit=limit,
    )
    current_ids = [item.movie_id for item in current.candidates[:limit]]
    no_ontology_ids = [item.movie_id for item in no_ontology.candidates[:limit]]
    current_rank = {item.movie_id: item.rank for item in current.candidates}
    no_ontology_rank = {item.movie_id: item.rank for item in no_ontology.candidates}
    return {
        **user,
        "preference_state": profile.short_term.preference_state.value,
        "drift_confidence": profile.short_term.drift_confidence,
        "top_overlap_ratio": round(len(set(current_ids) & set(no_ontology_ids)) / limit, 6),
        "current": current_metrics,
        "no_ontology": no_ontology_metrics,
        "family_contribution": family_contribution(current.candidates),
        "ontology_base_share": ontology_base_share(current.candidates[:limit]),
        "semantic_rank_effect": semantic_rank_effect(
            current.candidates,
            current_rank=current_rank,
            no_ontology_rank=no_ontology_rank,
        ),
    }


def policy_metrics(candidates, *, metadata, long_genres, short_genres, limit: int) -> dict:
    selected = candidates[:limit]
    rows = [
        {
            "movie_id": item.movie_id,
            "genre_ids": metadata.get(item.movie_id, {}).get("genre_ids", []),
        }
        for item in selected
    ]
    return {
        "long_genre_share": genre_alignment(rows, long_genres)["mean_genre_share"],
        "short_genre_share": genre_alignment(rows, short_genres)["mean_genre_share"],
        "short_only_ratio": round(
            sum(
                item.candidate.sources == (CandidateSource.SHORT_TERM_CONTEXT,)
                for item in selected
            )
            / len(selected),
            6,
        )
        if selected
        else 0.0,
    }


def family_contribution(candidates) -> dict:
    totals: dict[str, float] = defaultdict(float)
    for candidate in candidates:
        for score in candidate.ontology.type_scores:
            totals[score.feature.value] += score.long_positive_score + (
                CURRENT_WEIGHTS.ontology_short_term_multiplier * score.short_positive_score
            )
    total = sum(totals.values())
    return {
        feature: {
            "raw": round(value, 6),
            "share": round(value / total, 6) if total else 0.0,
        }
        for feature, value in sorted(totals.items())
    }


def ontology_base_share(candidates) -> dict:
    by_source: dict[str, list[float]] = defaultdict(list)
    for candidate in candidates:
        source = "+".join(item.value for item in candidate.candidate.sources)
        base = candidate.score.base_score
        by_source[source].append(candidate.score.ontology_component / base if base else 0.0)
    return {
        source: round(mean(values), 6)
        for source, values in sorted(by_source.items())
    }


def semantic_rank_effect(candidates, *, current_rank: dict[int, int], no_ontology_rank: dict[int, int]) -> dict:
    genre_only: list[int] = []
    semantic_supported: list[int] = []
    for candidate in candidates:
        scores = {item.feature.value: item for item in candidate.ontology.type_scores}
        genre = scores["genre"].long_positive_score + scores["genre"].short_positive_score
        semantic = sum(
            scores[feature].long_positive_score + scores[feature].short_positive_score
            for feature in ("theme", "mood")
        )
        if genre <= 0 or candidate.movie_id not in no_ontology_rank:
            continue
        uplift = no_ontology_rank[candidate.movie_id] - current_rank[candidate.movie_id]
        (semantic_supported if semantic > 0 else genre_only).append(uplift)
    return {
        "genre_only_count": len(genre_only),
        "genre_only_mean_rank_uplift": rounded_mean(genre_only),
        "theme_or_mood_count": len(semantic_supported),
        "theme_or_mood_mean_rank_uplift": rounded_mean(semantic_supported),
    }


def summarize(rows: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["profile_type"]].append(row)
    profile_summary = {}
    for profile_type in POST_MODEL_PROFILE_TYPES:
        values = by_type[profile_type]
        profile_summary[profile_type] = {
            "user_count": len(values),
            "top20_overlap_ratio": rounded_mean(item["top_overlap_ratio"] for item in values),
            "current_long_genre_share": rounded_mean(
                item["current"]["long_genre_share"] for item in values
            ),
            "no_ontology_long_genre_share": rounded_mean(
                item["no_ontology"]["long_genre_share"] for item in values
            ),
            "current_short_genre_share": rounded_mean(
                item["current"]["short_genre_share"] for item in values
            ),
            "no_ontology_short_genre_share": rounded_mean(
                item["no_ontology"]["short_genre_share"] for item in values
            ),
            "current_short_only_ratio": rounded_mean(
                item["current"]["short_only_ratio"] for item in values
            ),
            "no_ontology_short_only_ratio": rounded_mean(
                item["no_ontology"]["short_only_ratio"] for item in values
            ),
        }
    family_raw: Counter[str] = Counter()
    for row in rows:
        family_raw.update(
            {
                feature: values["raw"]
                for feature, values in row["family_contribution"].items()
            }
        )
    family_total = sum(family_raw.values())
    return {
        "by_profile_type": profile_summary,
        "family_share": {
            feature: round(value / family_total, 6) if family_total else 0.0
            for feature, value in family_raw.most_common()
        },
        "ontology_base_share": aggregate_source_values(rows, "ontology_base_share"),
        "semantic_rank_effect": {
            key: rounded_mean(
                row["semantic_rank_effect"][key]
                for row in rows
                if row["semantic_rank_effect"][key.replace("mean_rank_uplift", "count")] > 0
            )
            for key in (
                "genre_only_mean_rank_uplift",
                "theme_or_mood_mean_rank_uplift",
            )
        },
    }


def aggregate_source_values(rows: list[dict], key: str) -> dict:
    sources = {
        source
        for row in rows
        for source in row[key]
    }
    return {
        source: rounded_mean(row[key][source] for row in rows if source in row[key])
        for source in sorted(sources)
    }


def rounded_mean(values) -> float:
    collected = list(values)
    return round(mean(collected), 6) if collected else 0.0


def weight_dict(weights: PolicyComponentWeights) -> dict:
    return {
        "personal": weights.personal,
        "ontology": weights.ontology,
        "ontology_short_term_multiplier": weights.ontology_short_term_multiplier,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# V3 Phase E ontology component ablation",
        "",
        f"- generated: `{report['generated_at']}`",
        f"- users: `{report['scope']['user_count']}` post-model users",
        f"- bundle: `{report['scope']['bundle_id']}`",
        "- comparison: personal/ontology `1.00/0.00` vs `0.75/0.25` on the same retrieval candidates",
        "",
        "## Profile comparison",
        "",
        "| profile | top20 overlap | long genre 0%→25% | short genre 0%→25% | short-only 0%→25% |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for profile_type in POST_MODEL_PROFILE_TYPES:
        item = report["summary"]["by_profile_type"][profile_type]
        lines.append(
            f"| {profile_type} | {item['top20_overlap_ratio']:.3f} | "
            f"{item['no_ontology_long_genre_share']:.3f}→{item['current_long_genre_share']:.3f} | "
            f"{item['no_ontology_short_genre_share']:.3f}→{item['current_short_genre_share']:.3f} | "
            f"{item['no_ontology_short_only_ratio']:.3f}→{item['current_short_only_ratio']:.3f} |"
        )
    lines.extend(["", "## Family contribution", ""])
    for feature, share in report["summary"]["family_share"].items():
        lines.append(f"- `{feature}`: {share:.3f}")
    lines.extend(["", "## Score and semantic diagnostics", ""])
    for source, share in report["summary"]["ontology_base_share"].items():
        lines.append(f"- `{source}` mean ontology/base share: {share:.3f}")
    semantic = report["summary"]["semantic_rank_effect"]
    lines.append(
        f"- genre-only mean rank uplift: {semantic['genre_only_mean_rank_uplift']:.3f}"
    )
    lines.append(
        f"- theme/mood-supported mean rank uplift: {semantic['theme_or_mood_mean_rank_uplift']:.3f}"
    )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
