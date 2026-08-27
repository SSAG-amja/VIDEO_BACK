from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.crud.recsys.recommendations import load_v3_candidate_rows
from app.db.session import SessionLocal
from app.models.genre import Genre
from app.models.mapping import movie_genres
from app.models.movie import Movie
from app.models.ontology import OntologyNode
from app.models.ontology_recommendations import OntologyRecommendation
from app.models.playlist import Playlist
from app.models.recommendation_runs import RecommendationRun
from app.models.user import User
from app.schemas.recsys import RecommendationMode
from app.services.recsys.v3.domain.feature_registry import FeatureName
from app.services.recsys.v3.domain.schemas import ProfileFeatureSignal
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.recommender import get_recommendations
from app.services.recsys.v3.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
)
from app.services.recsys.v3.serving.serving_bundle import get_active_serving_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "z_v3_docs" / "diagnostics"
REPRESENTATIVE_PROFILE_TYPES = ("stable", "mixed", "drift", "negative_heavy")
COHORT_NAMES = {
    1: "action_crime_thriller",
    2: "romance_drama_comedy",
    3: "horror_mystery_thriller",
    4: "animation_family_adventure",
    5: "scifi_fantasy_adventure",
    6: "documentary_history_war",
}
COHORT_OPPOSITES = {1: 2, 2: 3, 3: 4, 4: 3, 5: 6, 6: 5}
POST_MODEL_USER_SCENARIOS = {
    25: "post_model_stable",
    26: "post_model_stable",
    27: "post_model_stable",
    28: "post_model_stable",
    29: "post_model_stable",
    30: "post_model_stable",
    37: "post_model_drift",
    38: "post_model_drift",
    39: "post_model_drift",
    58: "post_model_drift",
    35: "post_model_drift",
    36: "post_model_drift",
}
POST_MODEL_PROFILE_TYPES = ("post_model_stable", "post_model_drift")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a representative V3 long/short/final recommendation quality snapshot."
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--scenario",
        choices=("representative", "post-model"),
        default="representative",
    )
    args = parser.parse_args()
    if args.limit <= 0 or args.limit > 100:
        raise ValueError("quality snapshot limit must be between 1 and 100")

    bundle = get_active_serving_bundle()
    redis = get_redis()
    with SessionLocal() as db:
        users = load_quality_users(db, scenario=args.scenario)
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

    profile_types = tuple(dict.fromkeys(item["profile_type"] for item in users))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "user_count": len(rows),
            "users_per_profile_type": 6,
            "scenario": args.scenario,
            "profile_types": list(profile_types),
            "recommendation_limit": args.limit,
            "model_build_id": bundle.model.model_build_id,
            "ontology_build_id": bundle.ontology_build_id,
            "candidate_snapshot_id": bundle.candidate_snapshot_id,
            "bundle_id": bundle.bundle_id,
            "metric_note": (
                "Genre overlap is a fixture-oriented sanity signal, not relevance ground truth. "
                "mean_genre_share discounts movies that match only one of several genres."
            ),
        },
        "summary": summarize(rows, profile_types=profile_types),
        "users": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_quality_snapshot_{timestamp}.json"
    markdown_path = args.output_dir / f"v3_quality_snapshot_{timestamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "summary": report["summary"]}, ensure_ascii=False))


def load_quality_users(db: Session, *, scenario: str) -> list[dict]:
    if scenario == "representative":
        return load_representative_users(db)
    if scenario == "post-model":
        return load_post_model_users(db)
    raise ValueError(f"unsupported quality scenario={scenario}")


def load_representative_users(db: Session) -> list[dict]:
    users = list(
        db.execute(
            select(User.id, User.email)
            .where(
                User.email.like("v3seed-train-%@pinlm.test"),
                User.deleted_at.is_(None),
            )
            .order_by(User.email)
        )
    )
    selected: dict[tuple[str, int], dict] = {}
    for user_id, email in users:
        user_number = int(email.removeprefix("v3seed-train-").split("@", 1)[0])
        profile_type = profile_type_for_number(user_number)
        if profile_type == "stable" and user_number <= 6:
            # Stage-2 intentionally mutates onboarding for users 1-6 after model training.
            continue
        cohort_id = 1 + ((user_number - 1) % 6)
        selected.setdefault(
            (profile_type, cohort_id),
            {
                "user_id": int(user_id),
                "email": email,
                "user_number": user_number,
                "profile_type": profile_type,
                "cohort_id": cohort_id,
                "cohort_name": COHORT_NAMES[cohort_id],
            },
        )
    expected = {
        (profile_type, cohort_id)
        for profile_type in REPRESENTATIVE_PROFILE_TYPES
        for cohort_id in COHORT_NAMES
    }
    missing = expected - set(selected)
    if missing:
        raise ValueError(f"representative V3 seed users are missing keys={sorted(missing)}")
    return [
        selected[key]
        for key in sorted(
            selected,
            key=lambda item: (
                REPRESENTATIVE_PROFILE_TYPES.index(item[0]),
                item[1],
            ),
        )
    ]


def load_post_model_users(db: Session) -> list[dict]:
    expected_numbers = set(POST_MODEL_USER_SCENARIOS)
    users = list(
        db.execute(
            select(User.id, User.email)
            .where(
                User.email.like("v3seed-train-%@pinlm.test"),
                User.deleted_at.is_(None),
            )
            .order_by(User.email)
        )
    )
    marker_user_ids = set(
        db.scalars(
            select(Playlist.user_id).where(
                Playlist.title.like("v3quality-postmodel-%")
            )
        )
    )
    selected: list[dict] = []
    found_numbers: set[int] = set()
    for user_id, email in users:
        user_number = int(email.removeprefix("v3seed-train-").split("@", 1)[0])
        if user_number not in expected_numbers:
            continue
        if int(user_id) not in marker_user_ids:
            raise ValueError(
                f"post-model quality action marker is missing user_number={user_number}"
            )
        profile_type = POST_MODEL_USER_SCENARIOS[user_number]
        cohort_id = 1 + ((user_number - 1) % 6)
        recent_cohort_id = (
            cohort_id
            if profile_type == "post_model_stable"
            else COHORT_OPPOSITES[cohort_id]
        )
        selected.append(
            {
                "user_id": int(user_id),
                "email": email,
                "user_number": user_number,
                "profile_type": profile_type,
                "cohort_id": cohort_id,
                "cohort_name": COHORT_NAMES[cohort_id],
                "recent_cohort_id": recent_cohort_id,
                "recent_cohort_name": COHORT_NAMES[recent_cohort_id],
            }
        )
        found_numbers.add(user_number)
    missing = expected_numbers - found_numbers
    if missing:
        raise ValueError(f"post-model quality users are missing user_numbers={sorted(missing)}")
    return sorted(
        selected,
        key=lambda item: (
            POST_MODEL_PROFILE_TYPES.index(item["profile_type"]),
            item["cohort_id"],
        ),
    )


def profile_type_for_number(user_number: int) -> str:
    if user_number <= 72:
        return "stable"
    if user_number <= 96:
        return "mixed"
    if user_number <= 108:
        return "drift"
    return "negative_heavy"


def analyze_user(
    db: Session,
    *,
    redis,
    bundle,
    user: dict,
    limit: int,
) -> dict:
    model_user_known = bundle.model.user_index(user["user_id"]) is not None
    profile_result = build_user_runtime_profile(
        db,
        user_id=user["user_id"],
        ontology_build_id=bundle.ontology_build_id,
        as_of=datetime.now(timezone.utc),
        model_user_known=model_user_known,
    )
    profile = profile_result.bundle
    long_rows = load_v3_candidate_rows(db, user_id=user["user_id"], limit=limit)
    short_result = retrieve_cached_short_term_candidates(
        db,
        redis=redis,
        ontology_build_id=bundle.ontology_build_id,
        profile=profile,
        limit=limit,
    )
    response = get_recommendations(
        db,
        user_id=user["user_id"],
        mode=RecommendationMode.ALL,
        limit=limit,
        offset=0,
        shuffle_seed=f"quality-{user['user_id']}",
    )
    request_rows = load_latest_request_rows(db, user["user_id"])
    candidate_slice_rows = request_rows["candidate_slice"]
    final_rows = request_rows["final_response"]
    request_path = request_rows["request_path"]

    long_candidates = [
        {
            "movie_id": int(row.movie_id),
            "rank": int(row.rank),
            "score": float(row.score),
            "source": row.source,
        }
        for row in long_rows
    ]
    short_candidates = [
        {
            "movie_id": item.movie_id,
            "rank": item.source_rank,
            "score": item.short_term_raw_score,
            "source": "short_term_context",
        }
        for item in short_result.candidates[:limit]
    ]
    final_candidates = [
        {
            "movie_id": int(row.movie_id),
            "rank": int(row.rank),
            "score": float(row.score),
            "source": row.source,
            "score_trace": (row.source_scores or {}).get("score_trace", {}),
            "ontology_type_scores": (row.source_scores or {}).get(
                "ontology_type_scores", {}
            ),
            "reasons": row.explanation_tags or [],
        }
        for row in final_rows
        if row.movie_id in response.movie_ids
    ]
    final_candidates.sort(key=lambda item: item["rank"])

    all_movie_ids = {
        item["movie_id"]
        for candidates in (long_candidates, short_candidates, final_candidates)
        for item in candidates
    }
    metadata = load_movie_metadata(db, all_movie_ids)
    long_candidates = attach_metadata(long_candidates, metadata)
    short_candidates = attach_metadata(short_candidates, metadata)
    final_candidates = attach_metadata(final_candidates, metadata)

    feature_labels = load_feature_labels(
        db,
        ontology_build_id=bundle.ontology_build_id,
        signals=(
            profile.long_term.positive_features
            + profile.short_term.positive_features
            + profile.long_term.negative_features
            + profile.short_term.negative_features
        ),
    )
    long_profile = summarize_profile_features(profile.long_term.positive_features, feature_labels)
    short_profile = summarize_profile_features(profile.short_term.positive_features, feature_labels)
    long_negative_profile = summarize_profile_features(
        profile.long_term.negative_features,
        feature_labels,
    )
    short_negative_profile = summarize_profile_features(
        profile.short_term.negative_features,
        feature_labels,
    )
    long_genre_ids = top_genre_ids(profile.long_term.positive_features)
    short_genre_ids = top_genre_ids(profile.short_term.positive_features)
    negative_genre_ids = top_genre_ids(
        profile.long_term.negative_features + profile.short_term.negative_features
    )
    excluded = profile.long_term.excluded_movie_ids | profile.short_term.recent_negative_movie_ids
    candidate_slice_source_counts = dict(
        Counter(row.source for row in candidate_slice_rows)
    )
    merge_diagnostics = request_path.get("candidate_merge", {})

    return {
        **user,
        "model_user_known": model_user_known,
        "profile": {
            "maturity": profile.long_term.maturity.value,
            "positive_pair_count": profile.long_term.positive_pair_count,
            "passed_pair_count": profile.long_term.passed_pair_count,
            "watched_pair_count": profile.long_term.watched_pair_count,
            "short_window_action_count": profile.short_term.window_action_count,
            "drift_confidence": round(profile.short_term.drift_confidence, 6),
            "drift_components": profile_result.diagnostics.drift_components,
            "long_positive": long_profile,
            "short_positive": short_profile,
            "long_negative": long_negative_profile,
            "short_negative": short_negative_profile,
        },
        "quality": {
            "long_candidates_vs_long_genres": genre_alignment(long_candidates, long_genre_ids),
            "short_candidates_vs_short_genres": genre_alignment(short_candidates, short_genre_ids),
            "final_vs_long_genres": genre_alignment(final_candidates, long_genre_ids),
            "final_vs_short_genres": genre_alignment(final_candidates, short_genre_ids),
            "final_vs_negative_genres": genre_alignment(
                final_candidates,
                negative_genre_ids,
            ),
            "final_negative_evidence": negative_evidence(final_candidates),
            "final_short_source_ratio": round(
                sum("short_term_context" in item["source"] for item in final_candidates)
                / len(final_candidates),
                6,
            )
            if final_candidates
            else 0.0,
            "excluded_movie_violations": sorted(
                item["movie_id"] for item in final_candidates if item["movie_id"] in excluded
            ),
            "duplicate_final_movie_count": len(final_candidates)
            - len({item["movie_id"] for item in final_candidates}),
            "short_cache_status": short_result.diagnostics.cache_status,
            "long_catalog_quality": catalog_quality(long_candidates),
            "short_catalog_quality": catalog_quality(short_candidates),
            "final_catalog_quality": catalog_quality(final_candidates),
            "final_source_counts": dict(Counter(item["source"] for item in final_candidates)),
            "source_survival": {
                "raw_short_term_count": int(
                    request_path.get("short_term_candidate_count") or 0
                ),
                "merged_short_source_count": int(
                    merge_diagnostics.get("selected_short_only_count", 0)
                    + merge_diagnostics.get("selected_overlap_count", 0)
                ),
                "merged_short_only_count": int(
                    merge_diagnostics.get("selected_short_only_count", 0)
                ),
                "merged_overlap_count": int(
                    merge_diagnostics.get("selected_overlap_count", 0)
                ),
                "eligible_short_source_count": sum(
                    count
                    for source, count in candidate_slice_source_counts.items()
                    if "short_term_context" in source
                ),
                "final_short_source_count": sum(
                    "short_term_context" in item["source"]
                    for item in final_candidates
                ),
            },
            "max_abs_long_candidate_score": round(
                max((abs(item["score"]) for item in long_candidates), default=0.0),
                6,
            ),
        },
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "final_recommendations": final_candidates,
    }


def load_latest_request_rows(db: Session, user_id: int) -> dict:
    request_id = db.scalar(
        select(OntologyRecommendation.request_id)
        .where(
            OntologyRecommendation.user_id == user_id,
            OntologyRecommendation.candidate_stage == "final_response",
        )
        .order_by(OntologyRecommendation.created_at.desc(), OntologyRecommendation.id.desc())
        .limit(1)
    )
    if request_id is None:
        return {
            "request_id": None,
            "request_path": {},
            "candidate_slice": [],
            "final_response": [],
        }
    rows = list(
        db.scalars(
            select(OntologyRecommendation)
            .where(
                OntologyRecommendation.request_id == request_id,
            )
            .order_by(
                OntologyRecommendation.candidate_stage,
                OntologyRecommendation.rank,
            )
        )
    )
    run = db.get(RecommendationRun, request_id)
    config_snapshot = run.config_snapshot if run is not None else {}
    request_path = (config_snapshot or {}).get("request_path", {})
    return {
        "request_id": request_id,
        "request_path": request_path,
        "candidate_slice": [
            row for row in rows if row.candidate_stage == "candidate_slice"
        ],
        "final_response": [
            row for row in rows if row.candidate_stage == "final_response"
        ],
    }


def load_movie_metadata(db: Session, movie_ids: set[int]) -> dict[int, dict]:
    metadata: dict[int, dict] = {}
    if not movie_ids:
        return metadata
    rows = db.execute(
        select(
            Movie.id,
            Movie.tmdb_id,
            Movie.title,
            Movie.title_ko,
            Movie.vote_average,
            Movie.vote_count,
            Movie.popularity,
            Genre.id,
            Genre.name,
            Genre.name_ko,
        )
        .select_from(Movie)
        .outerjoin(movie_genres, movie_genres.c.movie_id == Movie.id)
        .outerjoin(Genre, Genre.id == movie_genres.c.genre_id)
        .where(Movie.id.in_(movie_ids))
    )
    for row in rows:
        movie = metadata.setdefault(
            int(row[0]),
            {
                "tmdb_id": row[1],
                "title": row[3] or row[2] or str(row[0]),
                "vote_average": row[4],
                "vote_count": row[5],
                "popularity": row[6],
                "genre_ids": [],
                "genres": [],
            },
        )
        if row[7] is not None:
            movie["genre_ids"].append(int(row[7]))
            movie["genres"].append(row[9] or row[8] or str(row[7]))
    return metadata


def attach_metadata(candidates: list[dict], metadata: dict[int, dict]) -> list[dict]:
    return [{**item, **metadata.get(item["movie_id"], {})} for item in candidates]


def top_genre_ids(signals: tuple[ProfileFeatureSignal, ...], limit: int = 3) -> set[int]:
    genres = sorted(
        (item for item in signals if item.feature == FeatureName.GENRE),
        key=lambda item: (-item.score, item.ref_id),
    )[:limit]
    return {int(item.ref_id) for item in genres}


def genre_alignment(candidates: list[dict], target_genre_ids: set[int]) -> dict:
    if not candidates or not target_genre_ids:
        return {
            "target_genre_ids": sorted(target_genre_ids),
            "candidate_count": len(candidates),
            "any_overlap_rate": 0.0,
            "mean_genre_share": 0.0,
        }
    overlaps = []
    shares = []
    for item in candidates:
        genre_ids = set(item.get("genre_ids", []))
        matched = genre_ids & target_genre_ids
        overlaps.append(bool(matched))
        shares.append(len(matched) / len(genre_ids) if genre_ids else 0.0)
    return {
        "target_genre_ids": sorted(target_genre_ids),
        "candidate_count": len(candidates),
        "any_overlap_rate": round(sum(overlaps) / len(overlaps), 6),
        "mean_genre_share": round(mean(shares), 6),
    }


def catalog_quality(candidates: list[dict]) -> dict:
    if not candidates:
        return {
            "candidate_count": 0,
            "zero_vote_ratio": 0.0,
            "low_vote_ratio": 0.0,
            "missing_genre_ratio": 0.0,
            "overbroad_genre_ratio": 0.0,
        }
    return {
        "candidate_count": len(candidates),
        "zero_vote_ratio": round(
            sum((item.get("vote_count") or 0) == 0 for item in candidates) / len(candidates),
            6,
        ),
        "low_vote_ratio": round(
            sum((item.get("vote_count") or 0) < 20 for item in candidates) / len(candidates),
            6,
        ),
        "missing_genre_ratio": round(
            sum(not item.get("genre_ids") for item in candidates) / len(candidates),
            6,
        ),
        "overbroad_genre_ratio": round(
            sum(len(item.get("genre_ids", [])) >= 8 for item in candidates) / len(candidates),
            6,
        ),
    }


def negative_evidence(candidates: list[dict]) -> dict:
    if not candidates:
        return {
            "candidate_count": 0,
            "matched_candidate_ratio": 0.0,
            "mean_raw_score": 0.0,
            "penalized_candidate_ratio": 0.0,
        }
    raw_scores = []
    penalties = []
    for candidate in candidates:
        raw_score = 0.0
        for values in candidate.get("ontology_type_scores", {}).values():
            raw_score += float(values.get("long_negative") or 0.0)
            raw_score += float(values.get("short_negative") or 0.0)
        raw_scores.append(raw_score)
        penalties.append(
            float(
                candidate.get("score_trace", {}).get(
                    "negative_preference_penalty", 0.0
                )
                or 0.0
            )
        )
    return {
        "candidate_count": len(candidates),
        "matched_candidate_ratio": round(
            sum(value > 0.0 for value in raw_scores) / len(raw_scores),
            6,
        ),
        "mean_raw_score": round(mean(raw_scores), 6),
        "penalized_candidate_ratio": round(
            sum(value > 0.0 for value in penalties) / len(penalties),
            6,
        ),
    }


def load_feature_labels(
    db: Session,
    *,
    ontology_build_id: int,
    signals: tuple[ProfileFeatureSignal, ...],
) -> dict[tuple[str, str], str]:
    requested = {(node_type(item.feature), item.ref_id) for item in signals}
    if not requested:
        return {}
    node_types = {item[0] for item in requested}
    ref_ids = {item[1] for item in requested}
    rows = db.execute(
        select(OntologyNode.node_type, OntologyNode.ref_id, OntologyNode.label)
        .where(
            OntologyNode.build_id == ontology_build_id,
            OntologyNode.node_type.in_(node_types),
            OntologyNode.ref_id.in_(ref_ids),
        )
    )
    return {(node_type_value, ref_id): label for node_type_value, ref_id, label in rows}


def node_type(feature: FeatureName) -> str:
    return "person" if feature in {FeatureName.ACTOR, FeatureName.DIRECTOR} else feature.value


def summarize_profile_features(
    signals: tuple[ProfileFeatureSignal, ...],
    labels: dict[tuple[str, str], str],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[ProfileFeatureSignal]] = defaultdict(list)
    for signal in signals:
        grouped[signal.feature.value].append(signal)
    return {
        feature: [
            {
                "ref_id": item.ref_id,
                "label": labels.get((node_type(item.feature), item.ref_id), item.ref_id),
                "score": round(item.score, 6),
                "source_actions": list(item.source_actions),
            }
            for item in sorted(items, key=lambda value: (-value.score, value.ref_id))[:3]
        ]
        for feature, items in sorted(grouped.items())
    }


def summarize(rows: list[dict], *, profile_types: tuple[str, ...]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["profile_type"]].append(row)
    by_profile = {}
    for profile_type in profile_types:
        items = grouped[profile_type]
        by_profile[profile_type] = {
            "user_count": len(items),
            "average_drift_confidence": rounded_mean(
                item["profile"]["drift_confidence"] for item in items
            ),
            "long_candidates_long_genre_share": rounded_mean(
                item["quality"]["long_candidates_vs_long_genres"]["mean_genre_share"]
                for item in items
            ),
            "short_candidates_short_genre_share": rounded_mean(
                item["quality"]["short_candidates_vs_short_genres"]["mean_genre_share"]
                for item in items
            ),
            "final_long_genre_share": rounded_mean(
                item["quality"]["final_vs_long_genres"]["mean_genre_share"] for item in items
            ),
            "final_short_genre_share": rounded_mean(
                item["quality"]["final_vs_short_genres"]["mean_genre_share"] for item in items
            ),
            "final_short_source_ratio": rounded_mean(
                item["quality"]["final_short_source_ratio"] for item in items
            ),
            "final_negative_genre_share": rounded_mean(
                item["quality"]["final_vs_negative_genres"]["mean_genre_share"]
                for item in items
            ),
            "final_negative_evidence_ratio": rounded_mean(
                item["quality"]["final_negative_evidence"]["matched_candidate_ratio"]
                for item in items
            ),
            "source_survival": {
                key: rounded_mean(
                    item["quality"]["source_survival"][key] for item in items
                )
                for key in (
                    "raw_short_term_count",
                    "merged_short_source_count",
                    "merged_short_only_count",
                    "merged_overlap_count",
                    "eligible_short_source_count",
                    "final_short_source_count",
                )
            },
            "average_final_count": rounded_mean(len(item["final_recommendations"]) for item in items),
            "final_zero_vote_ratio": rounded_mean(
                item["quality"]["final_catalog_quality"]["zero_vote_ratio"] for item in items
            ),
            "final_low_vote_ratio": rounded_mean(
                item["quality"]["final_catalog_quality"]["low_vote_ratio"] for item in items
            ),
            "final_missing_genre_ratio": rounded_mean(
                item["quality"]["final_catalog_quality"]["missing_genre_ratio"] for item in items
            ),
            "final_overbroad_genre_ratio": rounded_mean(
                item["quality"]["final_catalog_quality"]["overbroad_genre_ratio"] for item in items
            ),
            "max_abs_long_candidate_score": max(
                item["quality"]["max_abs_long_candidate_score"] for item in items
            ),
            "excluded_violation_count": sum(
                len(item["quality"]["excluded_movie_violations"]) for item in items
            ),
        }
    long_movie_ids = [
        candidate["movie_id"]
        for item in rows
        for candidate in item["long_candidates"]
    ]
    short_movie_ids = [
        candidate["movie_id"]
        for item in rows
        for candidate in item["short_candidates"]
    ]
    final_movie_ids = [
        candidate["movie_id"]
        for item in rows
        for candidate in item["final_recommendations"]
    ]
    final_top5_movie_ids = [
        candidate["movie_id"]
        for item in rows
        for candidate in item["final_recommendations"][:5]
    ]
    long_abs_scores = sorted(
        abs(candidate["score"])
        for item in rows
        for candidate in item["long_candidates"]
    )
    return {
        "by_profile_type": by_profile,
        "candidate_concentration": {
            "long_slots": len(long_movie_ids),
            "long_unique_movies": len(set(long_movie_ids)),
            "short_slots": len(short_movie_ids),
            "short_unique_movies": len(set(short_movie_ids)),
            "final_slots": len(final_movie_ids),
            "final_unique_movies": len(set(final_movie_ids)),
            "final_top5_slots": len(final_top5_movie_ids),
            "final_top5_unique_movies": len(set(final_top5_movie_ids)),
        },
        "long_raw_score_distribution": score_distribution(long_abs_scores),
        "short_cache_status_counts": dict(
            Counter(item["quality"]["short_cache_status"] for item in rows)
        ),
        "total_excluded_violation_count": sum(
            len(item["quality"]["excluded_movie_violations"]) for item in rows
        ),
        "total_duplicate_final_movie_count": sum(
            item["quality"]["duplicate_final_movie_count"] for item in rows
        ),
        "final_source_counts": dict(
            Counter(
                candidate["source"]
                for item in rows
                for candidate in item["final_recommendations"]
            )
        ),
    }


def score_distribution(values: list[float]) -> dict:
    if not values:
        return {"min": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "min": values[0],
        "median": values[len(values) // 2],
        "p95": values[min(int(len(values) * 0.95), len(values) - 1)],
        "max": values[-1],
    }


def rounded_mean(values) -> float:
    collected = list(values)
    return round(mean(collected), 6) if collected else 0.0


def render_markdown(report: dict) -> str:
    profile_types = tuple(report["scope"]["profile_types"])
    lines = [
        "# V3 장기·단기 추천 간이 품질 분석",
        "",
        f"- 생성 시각: `{report['generated_at']}`",
        f"- 시나리오: `{report['scope']['scenario']}`",
        f"- 사용자: `{report['scope']['user_count']}`명 (유형별 6개 취향 cohort)",
        f"- 후보: 장기·단기·최종 각각 상위 `{report['scope']['recommendation_limit']}`개",
        "- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.",
        "- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.",
        "",
        "## 유형별 요약",
        "",
        "| 유형 | drift | 장기 후보→장기 | 단기 후보→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile_type in profile_types:
        item = report["summary"]["by_profile_type"][profile_type]
        lines.append(
            f"| {profile_type} | {item['average_drift_confidence']:.3f} | "
            f"{item['long_candidates_long_genre_share']:.3f} | "
            f"{item['short_candidates_short_genre_share']:.3f} | "
            f"{item['final_long_genre_share']:.3f} | "
            f"{item['final_short_genre_share']:.3f} | "
            f"{item['final_short_source_ratio']:.3f} | {item['average_final_count']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Catalog 품질 요약",
            "",
            "| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for profile_type in profile_types:
        item = report["summary"]["by_profile_type"][profile_type]
        lines.append(
            f"| {profile_type} | {item['final_zero_vote_ratio']:.3f} | "
            f"{item['final_low_vote_ratio']:.3f} | {item['final_missing_genre_ratio']:.3f} | "
            f"{item['final_overbroad_genre_ratio']:.3f} | {item['max_abs_long_candidate_score']:.3e} |"
        )
    lines.extend(
        [
            "",
            "## Negative 취향 잔존",
            "",
            "| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |",
            "| --- | ---: | ---: |",
        ]
    )
    for profile_type in profile_types:
        item = report["summary"]["by_profile_type"][profile_type]
        lines.append(
            f"| {profile_type} | {item['final_negative_genre_share']:.3f} | "
            f"{item['final_negative_evidence_ratio']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 단기 후보 단계별 생존",
            "",
            "| 유형 | 원본 단기 | 병합 150 단기 | 병합 단기 전용 | 병합 중복 | eligibility 100 | 최종 20 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for profile_type in profile_types:
        survival = report["summary"]["by_profile_type"][profile_type]["source_survival"]
        lines.append(
            f"| {profile_type} | {survival['raw_short_term_count']:.1f} | "
            f"{survival['merged_short_source_count']:.1f} | "
            f"{survival['merged_short_only_count']:.1f} | "
            f"{survival['merged_overlap_count']:.1f} | "
            f"{survival['eligible_short_source_count']:.1f} | "
            f"{survival['final_short_source_count']:.1f} |"
        )
    concentration = report["summary"]["candidate_concentration"]
    score_stats = report["summary"]["long_raw_score_distribution"]
    lines.extend(
        [
            "",
            "## 후보 집중도와 장기 점수",
            "",
            f"- 장기: `{concentration['long_slots']}`칸 / 고유 `{concentration['long_unique_movies']}`편",
            f"- 단기: `{concentration['short_slots']}`칸 / 고유 `{concentration['short_unique_movies']}`편",
            f"- 최종: `{concentration['final_slots']}`칸 / 고유 `{concentration['final_unique_movies']}`편",
            f"- 최종 상위 5: `{concentration['final_top5_slots']}`칸 / 고유 `{concentration['final_top5_unique_movies']}`편",
            f"- 장기 raw score 절댓값: min `{score_stats['min']:.3e}`, median `{score_stats['median']:.3e}`, p95 `{score_stats['p95']:.3e}`, max `{score_stats['max']:.3e}`",
        ]
    )
    lines.extend(
        [
            "",
            "## 사용자별 요약",
            "",
            "| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | drift | 최종→장기 | 최종→단기 | 단기 source |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for user in report["users"]:
        long_genres = feature_labels(user["profile"]["long_positive"], "genre")
        short_genres = feature_labels(user["profile"]["short_positive"], "genre")
        quality = user["quality"]
        cohort = user["cohort_name"]
        if user.get("recent_cohort_name"):
            cohort = f"{cohort} → {user['recent_cohort_name']}"
        lines.append(
            f"| {user['email']} | {user['profile_type']} | {cohort} | "
            f"{long_genres} | {short_genres} | {user['profile']['drift_confidence']:.3f} | "
            f"{quality['final_vs_long_genres']['mean_genre_share']:.3f} | "
            f"{quality['final_vs_short_genres']['mean_genre_share']:.3f} | "
            f"{quality['final_short_source_ratio']:.3f} |"
        )
    lines.extend(["", "## 최종 추천 표본", ""])
    for user in report["users"]:
        top_movies = ", ".join(
            f"{item.get('title', item['movie_id'])} (TMDB {item.get('tmdb_id')})"
            for item in user["final_recommendations"][:5]
        )
        lines.append(f"- `{user['email']}`: {top_movies}")
    lines.extend(
        [
            "",
            "## 불변식",
            "",
            f"- 제외 영화 노출: `{report['summary']['total_excluded_violation_count']}`건",
            f"- 최종 중복: `{report['summary']['total_duplicate_final_movie_count']}`건",
            "",
        ]
    )
    return "\n".join(lines)


def feature_labels(profile: dict[str, list[dict]], feature: str) -> str:
    values = profile.get(feature, [])
    return ", ".join(item["label"] for item in values) if values else "없음"


if __name__ == "__main__":
    main()
