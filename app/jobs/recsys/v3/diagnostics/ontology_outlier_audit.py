from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from sqlalchemy import text

from app.db.session import SessionLocal
from app.jobs.recsys.v3.diagnostics.quality_snapshot import DEFAULT_OUTPUT_DIR


COHORT_GENRE_IDS = {
    "action_crime_thriller": {28, 53, 80},
    "romance_drama_comedy": {18, 35, 10749},
    "horror_mystery_thriller": {27, 53, 9648},
    "animation_family_adventure": {12, 16, 10751},
    "scifi_fantasy_adventure": {12, 14, 878},
    "documentary_history_war": {36, 99, 10752},
}
FEATURE_RELATIONS = {
    "genre": "has_genre",
    "keyword": "has_keyword",
    "actor": "has_actor",
    "director": "has_director",
    "theme": "has_theme",
    "mood": "has_mood",
}
PROFILE_SCOPES = (
    "long_positive",
    "short_positive",
    "long_negative",
    "short_negative",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit suspicious V3 recommendations against exact ontology feature matches."
    )
    parser.add_argument("quality_snapshot", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    snapshot = json.loads(args.quality_snapshot.read_text(encoding="utf-8"))
    repeated_top5 = repeated_top5_movies(snapshot)
    anomalies = select_anomalies(snapshot, repeated_top5=repeated_top5)
    movie_ids = sorted({item["movie_id"] for item in anomalies})
    with SessionLocal() as db:
        graph_features = load_graph_features(
            db,
            ontology_build_id=int(snapshot["scope"]["ontology_build_id"]),
            movie_ids=movie_ids,
        )
    users = {int(user["user_id"]): user for user in snapshot["users"]}
    for anomaly in anomalies:
        user = users[anomaly["user_id"]]
        anomaly["ontology_matches"] = matched_profile_features(
            user["profile"],
            graph_features.get(anomaly["movie_id"], ()),
        )
        anomaly["diagnosis"] = diagnose(anomaly)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": str(args.quality_snapshot),
        "scope": audit_scope(snapshot),
        "rules": {
            "drift_top5_old_only": (
                "drift top-5 model candidate overlaps historical cohort genres but not recent cohort genres"
            ),
            "top10_no_current_genre": "top-10 candidate has no current target genre",
            "top10_low_vote": "top-10 candidate has fewer than 20 votes",
            "overbroad_catalog_genres": "candidate has eight or more catalog genres",
            "cross_user_top5_repeat": "same movie appears in at least three users' top-5",
            "high_negative_conflict": (
                "top-10 candidate keeps at least 0.10 semantic-negative penalty"
            ),
        },
        "summary": summarize(anomalies),
        "top5_alignment": top5_alignment(snapshot),
        "repeated_top5": repeated_top5,
        "anomalies": anomalies,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"v3_ontology_outlier_audit_{timestamp}.json"
    markdown_path = args.output_dir / f"v3_ontology_outlier_audit_{timestamp}.md"
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


def audit_scope(snapshot: dict) -> dict:
    counts = Counter(user["profile_type"] for user in snapshot["users"])
    cohorts: dict[str, list[str]] = defaultdict(list)
    for user in snapshot["users"]:
        cohorts[user["profile_type"]].append(
            f"{user['cohort_name']} -> {user['recent_cohort_name']}"
        )
    return {
        "user_count": len(snapshot["users"]),
        "profile_type_counts": dict(sorted(counts.items())),
        "cohorts_by_profile_type": {
            profile_type: values for profile_type, values in sorted(cohorts.items())
        },
        "recommendations_per_user": int(snapshot["scope"]["recommendation_limit"]),
        "audited_recommendation_count": sum(
            len(user["final_recommendations"]) for user in snapshot["users"]
        ),
        "ontology_build_id": int(snapshot["scope"]["ontology_build_id"]),
        "bundle_id": snapshot["scope"]["bundle_id"],
    }


def repeated_top5_movies(snapshot: dict) -> list[dict]:
    occurrences: dict[int, list[dict]] = defaultdict(list)
    for user in snapshot["users"]:
        for candidate in user["final_recommendations"][:5]:
            occurrences[int(candidate["movie_id"])].append(
                {
                    "user_number": int(user["user_number"]),
                    "profile_type": user["profile_type"],
                    "rank": int(candidate["rank"]),
                    "source": candidate["source"],
                }
            )
    repeated = []
    for movie_id, rows in occurrences.items():
        if len(rows) < 3:
            continue
        candidate = next(
            item
            for user in snapshot["users"]
            for item in user["final_recommendations"][:5]
            if int(item["movie_id"]) == movie_id
        )
        repeated.append(
            {
                "movie_id": movie_id,
                "tmdb_id": candidate.get("tmdb_id"),
                "title": candidate.get("title"),
                "user_count": len(rows),
                "occurrences": rows,
            }
        )
    return sorted(repeated, key=lambda item: (-item["user_count"], item["movie_id"]))


def select_anomalies(snapshot: dict, *, repeated_top5: list[dict]) -> list[dict]:
    repeated_ids = {int(item["movie_id"]) for item in repeated_top5}
    anomalies = []
    for user in snapshot["users"]:
        current_genres = COHORT_GENRE_IDS[user["recent_cohort_name"]]
        historical_genres = COHORT_GENRE_IDS[user["cohort_name"]]
        for candidate in user["final_recommendations"]:
            rank = int(candidate["rank"])
            movie_genres = {int(value) for value in candidate.get("genre_ids", ())}
            trace = candidate.get("score_trace") or {}
            rules = []
            if (
                user["profile_type"] == "post_model_drift"
                and rank <= 5
                and candidate["source"] == "model"
                and movie_genres & historical_genres
                and not movie_genres & current_genres
            ):
                rules.append("drift_top5_old_only")
            if rank <= 10 and not movie_genres & current_genres:
                rules.append("top10_no_current_genre")
            if rank <= 10 and int(candidate.get("vote_count") or 0) < 20:
                rules.append("top10_low_vote")
            if len(movie_genres) >= 8:
                rules.append("overbroad_catalog_genres")
            if rank <= 5 and int(candidate["movie_id"]) in repeated_ids:
                rules.append("cross_user_top5_repeat")
            if rules and rank <= 10 and float(
                trace.get("negative_preference_penalty") or 0.0
            ) >= 0.10:
                rules.append("high_negative_conflict")
            if not rules:
                continue
            anomalies.append(
                {
                    "user_id": int(user["user_id"]),
                    "user_number": int(user["user_number"]),
                    "profile_type": user["profile_type"],
                    "historical_cohort": user["cohort_name"],
                    "recent_cohort": user["recent_cohort_name"],
                    "movie_id": int(candidate["movie_id"]),
                    "tmdb_id": candidate.get("tmdb_id"),
                    "title": candidate.get("title"),
                    "rank": rank,
                    "source": candidate["source"],
                    "genres": candidate.get("genres", []),
                    "genre_ids": sorted(movie_genres),
                    "vote_count": int(candidate.get("vote_count") or 0),
                    "rules": rules,
                    "scores": {
                        "normalized_long_term": float(
                            trace.get("normalized_long_term_score") or 0.0
                        ),
                        "normalized_short_term": float(
                            trace.get("normalized_short_term_score") or 0.0
                        ),
                        "normalized_ontology": float(
                            trace.get("normalized_ontology_score") or 0.0
                        ),
                        "ontology_component": float(trace.get("ontology_component") or 0.0),
                        "catalog_trust_penalty": float(
                            trace.get("catalog_trust_penalty") or 0.0
                        ),
                        "negative_penalty": float(
                            trace.get("negative_preference_penalty") or 0.0
                        ),
                        "final": float(trace.get("final_score") or 0.0),
                        "short_term_lane_forced": bool(
                            trace.get("short_term_lane_forced")
                        ),
                    },
                    "ontology_family_scores": candidate.get("ontology_type_scores", {}),
                }
            )
    return sorted(anomalies, key=lambda item: (item["user_number"], item["rank"]))


def load_graph_features(db, *, ontology_build_id: int, movie_ids: list[int]) -> dict[int, list[dict]]:
    if not movie_ids:
        return {}
    rows = db.execute(
        text(
            """
            WITH candidate_input(movie_id) AS (
                SELECT unnest(CAST(:movie_ids AS integer[]))
            )
            SELECT candidate_input.movie_id,
                   edge.relation_type,
                   feature.ref_id,
                   COALESCE(feature.label_ko, feature.label, feature.ref_id) AS label,
                   COALESCE(edge.effective_strength, edge.weight * edge.confidence) AS strength,
                   edge.evidence_count,
                   edge.source
            FROM candidate_input
            JOIN ontology_nodes movie
              ON movie.build_id = :build_id
             AND movie.node_type = 'movie'
             AND movie.ref_id = candidate_input.movie_id::text
             AND movie.is_active IS TRUE
            JOIN ontology_edges edge
              ON edge.build_id = :build_id
             AND edge.source_node_id = movie.id
             AND edge.relation_type = ANY(CAST(:relations AS text[]))
            JOIN ontology_nodes feature
              ON feature.id = edge.target_node_id
             AND feature.build_id = :build_id
             AND feature.is_active IS TRUE
            ORDER BY candidate_input.movie_id, edge.relation_type, feature.ref_id
            """
        ),
        {
            "build_id": ontology_build_id,
            "movie_ids": movie_ids,
            "relations": list(FEATURE_RELATIONS.values()),
        },
    )
    by_movie: dict[int, list[dict]] = defaultdict(list)
    relation_features = {relation: feature for feature, relation in FEATURE_RELATIONS.items()}
    for movie_id, relation, ref_id, label, strength, evidence_count, source in rows:
        by_movie[int(movie_id)].append(
            {
                "feature": relation_features[str(relation)],
                "ref_id": str(ref_id),
                "label": str(label),
                "strength": float(strength),
                "evidence_count": int(evidence_count),
                "source": str(source),
            }
        )
    return by_movie


def matched_profile_features(profile: dict, graph_features: list[dict]) -> dict:
    edge_index = {
        (item["feature"], item["ref_id"]): item for item in graph_features
    }
    result = {}
    for scope in PROFILE_SCOPES:
        matches = []
        for feature, signals in (profile.get(scope) or {}).items():
            for signal in signals:
                edge = edge_index.get((feature, str(signal["ref_id"])))
                if edge is None:
                    continue
                profile_score = float(signal["score"])
                matches.append(
                    {
                        **edge,
                        "profile_score": profile_score,
                        "contribution": round(profile_score * edge["strength"], 6),
                        "profile_actions": signal.get("source_actions", []),
                    }
                )
        result[scope] = sorted(
            matches,
            key=lambda item: (-item["contribution"], item["feature"], item["ref_id"]),
        )
    return result


def diagnose(anomaly: dict) -> list[str]:
    diagnoses = []
    rules = set(anomaly["rules"])
    scores = anomaly["scores"]
    matches = anomaly["ontology_matches"]
    if "drift_top5_old_only" in rules:
        diagnoses.append(
            "Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5."
        )
    elif (
        "top10_no_current_genre" in rules
        and anomaly["source"] == "model"
        and scores["normalized_long_term"] >= 0.7
    ):
        diagnoses.append(
            "Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10."
        )
    if "overbroad_catalog_genres" in rules:
        diagnoses.append(
            "Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts."
        )
    if "cross_user_top5_repeat" in rules and scores["normalized_long_term"] >= 0.8:
        diagnoses.append(
            "Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles."
        )
    if "top10_no_current_genre" in rules and scores["normalized_ontology"] >= 0.7:
        positive_non_genre = [
            item
            for scope in ("long_positive", "short_positive")
            for item in matches[scope]
            if item["feature"] != "genre"
        ]
        if positive_non_genre:
            diagnoses.append(
                "Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres."
            )
    if "high_negative_conflict" in rules:
        diagnoses.append(
            "Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap."
        )
    if "top10_low_vote" in rules:
        diagnoses.append(
            "Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate."
        )
    return diagnoses


def summarize(anomalies: list[dict]) -> dict:
    rule_counts = Counter(rule for item in anomalies for rule in item["rules"])
    diagnoses = Counter(
        diagnosis for item in anomalies for diagnosis in item.get("diagnosis", ())
    )
    return {
        "anomaly_row_count": len(anomalies),
        "unique_user_count": len({item["user_id"] for item in anomalies}),
        "unique_movie_count": len({item["movie_id"] for item in anomalies}),
        "rule_counts": dict(sorted(rule_counts.items())),
        "repeated_top5_current_mismatch_count": sum(
            "cross_user_top5_repeat" in item["rules"]
            and "top10_no_current_genre" in item["rules"]
            for item in anomalies
        ),
        "diagnosis_counts": dict(sorted(diagnoses.items())),
    }


def top5_alignment(snapshot: dict) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for user in snapshot["users"]:
        grouped[user["profile_type"]].append(user)
    result = {}
    for profile_type, users in sorted(grouped.items()):
        slot_count = len(users) * 5
        current_matches = 0
        historical_only = 0
        no_current_match = 0
        for user in users:
            current = COHORT_GENRE_IDS[user["recent_cohort_name"]]
            historical = COHORT_GENRE_IDS[user["cohort_name"]]
            for candidate in user["final_recommendations"][:5]:
                genres = {int(value) for value in candidate.get("genre_ids", ())}
                if genres & current:
                    current_matches += 1
                else:
                    no_current_match += 1
                    historical_only += int(bool(genres & historical))
        result[profile_type] = {
            "user_count": len(users),
            "slot_count": slot_count,
            "current_genre_match_count": current_matches,
            "current_genre_match_ratio": round(current_matches / slot_count, 6),
            "no_current_genre_count": no_current_match,
            "historical_only_count": historical_only,
        }
    return result


def top_matches(anomaly: dict, scope: str, limit: int = 4) -> str:
    rows = anomaly["ontology_matches"][scope][:limit]
    if not rows:
        return "-"
    return ", ".join(
        f"{item['feature']}:{item['label']}({item['contribution']:.2f})"
        for item in rows
    )


def render_markdown(report: dict) -> str:
    scope = report["scope"]
    lines = [
        "# V3 Ontology Outlier Audit",
        "",
        "## Audit Scope",
        "",
        f"- users: `{scope['user_count']}`",
        f"- post_model_stable: `{scope['profile_type_counts'].get('post_model_stable', 0)}`",
        f"- post_model_drift: `{scope['profile_type_counts'].get('post_model_drift', 0)}`",
        f"- recommendations: `{scope['audited_recommendation_count']}` (top {scope['recommendations_per_user']} per user)",
        f"- ontology build: `{scope['ontology_build_id']}`",
        "- interpretation: ontology matches explain the explicit semantic component, not LightFM's internal causal reason",
        "",
        "## Cohorts",
        "",
    ]
    for profile_type, cohorts in scope["cohorts_by_profile_type"].items():
        lines.append(f"- `{profile_type}`: " + "; ".join(cohorts))
    lines.extend(
        (
            "",
            "## Summary",
            "",
            f"- anomaly rows: `{report['summary']['anomaly_row_count']}`",
            f"- affected users: `{report['summary']['unique_user_count']}`",
            f"- unique movies: `{report['summary']['unique_movie_count']}`",
            "- rule counts: `" + json.dumps(report["summary"]["rule_counts"], ensure_ascii=False) + "`",
            f"- repeated top-5 occurrences outside current genres: `{report['summary']['repeated_top5_current_mismatch_count']}`",
            "",
            "## Top-5 Cohort Alignment",
            "",
            "| profile | slots | current genre match | no current genre | historical only |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for profile_type, values in report["top5_alignment"].items():
        lines.append(
            f"| {profile_type} | {values['slot_count']} | "
            f"{values['current_genre_match_count']} ({values['current_genre_match_ratio']:.1%}) | "
            f"{values['no_current_genre_count']} | {values['historical_only_count']} |"
        )
    lines.extend(
        (
            "",
            "## Repeated Top-5 Movies",
            "",
            "| movie | users |",
            "| --- | ---: |",
        )
    )
    for item in report["repeated_top5"]:
        lines.append(f"| {item['title']} (TMDB {item['tmdb_id']}) | {item['user_count']} |")
    lines.extend(
        (
            "",
            "## Outliers And Ontology Evidence",
            "",
            "| user | state | rank | movie | source | rules | long evidence | short evidence | negative evidence |",
            "| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |",
        )
    )
    for item in report["anomalies"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    str(item["user_number"]),
                    item["profile_type"],
                    str(item["rank"]),
                    f"{item['title']} (TMDB {item['tmdb_id']})",
                    item["source"],
                    ", ".join(item["rules"]),
                    top_matches(item, "long_positive"),
                    top_matches(item, "short_positive"),
                    top_matches(item, "long_negative", 2)
                    + "; "
                    + top_matches(item, "short_negative", 2),
                )
            )
            + " |"
        )
    lines.extend(("", "## Diagnosis", ""))
    for index, item in enumerate(report["anomalies"], 1):
        lines.append(
            f"{index}. user {item['user_number']} / rank {item['rank']} / "
            f"{item['title']}: " + " ".join(item["diagnosis"])
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
