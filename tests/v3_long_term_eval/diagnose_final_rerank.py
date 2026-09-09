from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.services.recsys.v3.config import (
    CANDIDATE_POOL_SIZE,
    CANDIDATE_STORAGE_SIZE,
    INITIAL_CANDIDATE_SCAN_SIZE,
)
from app.services.recsys.v3.domain.schemas import OttFilterMode
from app.services.recsys.v3.policy.policy_engine import evaluate_candidate_set
from app.services.recsys.v3.policy.policy_schemas import PolicyRequestContext
from app.services.recsys.v3.profiles.profile_builder import build_user_runtime_profile
from app.services.recsys.v3.retrieval.candidate_eligibility import (
    select_eligible_candidates,
)
from app.services.recsys.v3.retrieval.candidate_merger import merge_candidates
from app.services.recsys.v3.retrieval.collaborative_confidence import (
    assess_user_collaborative_confidence,
)
from app.services.recsys.v3.retrieval.long_term_ontology_retriever import (
    retrieve_long_term_ontology_candidates,
)
from app.services.recsys.v3.retrieval.lightfm_retriever import (
    retrieve_lightfm_candidates,
)
from app.services.recsys.v3.retrieval.initial_candidate_filter import (
    select_initial_candidates,
)
from app.services.recsys.v3.retrieval.ontology_analyzer import analyze_candidates
from app.services.recsys.v3.retrieval.short_term_candidate_cache import (
    retrieve_cached_short_term_candidates,
)
from app.services.recsys.v3.serving.model_store import load_runtime_hybrid_artifact
from tests.v3_long_term_eval.analyze_single_user import (
    enrich_metadata,
    load_db_metadata,
    load_jsonl_record,
    load_movielens_movies,
    load_source_by_tmdb,
)
from tests.v3_long_term_eval.metrics import evaluate_ranking, rating_relevance


ROOT = Path("tests/v3_long_term_eval")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trace one user's candidates through V3 merge and policy reranking"
    )
    parser.add_argument("source_user_id", type=int)
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--plan", type=Path, default=ROOT / "generated/evaluation_plan.jsonl"
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=ROOT / "outputs/long_term_evaluation_results.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/final_rerank_diagnostics.json",
    )
    parser.add_argument(
        "--disable-mature-cold-item-gate",
        action="store_true",
        help="Disable the known-user mature cold-item gate for before/after comparison",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = load_jsonl_record(args.plan, args.source_user_id)
    previous_result = load_jsonl_record(args.results, args.source_user_id)
    plan["user_id"] = int(previous_result["user_id"])
    artifact = load_runtime_hybrid_artifact(args.artifact)
    as_of = datetime.fromisoformat(str(plan["evaluation_as_of"]))
    blocked_movie_ids = frozenset(int(item["movie_id"]) for item in plan["train"])
    context = PolicyRequestContext(
        as_of=as_of,
        limit=CANDIDATE_POOL_SIZE,
        blocked_movie_ids=blocked_movie_ids,
    )

    with SessionLocal() as db:
        profile = build_user_runtime_profile(
            db,
            user_id=int(plan["user_id"]),
            ontology_build_id=artifact.ontology_build_id,
            as_of=as_of,
            model_user_known=True,
            ott_mode=OttFilterMode.ALL,
        ).bundle
        raw_long_term_candidates = retrieve_lightfm_candidates(
            artifact,
            profile=profile,
            excluded_movie_ids=blocked_movie_ids,
            limit=INITIAL_CANDIDATE_SCAN_SIZE,
        )
        initial_model = select_initial_candidates(
            db,
            candidates=raw_long_term_candidates,
            as_of=as_of,
            movie_identity_supported=(
                None
                if args.disable_mature_cold_item_gate
                else artifact.movie_identity_supported
            ),
            limit=CANDIDATE_STORAGE_SIZE,
        )
        long_term_candidates = tuple(initial_model.candidates)
        short_term = retrieve_cached_short_term_candidates(
            db,
            redis=None,
            ontology_build_id=artifact.ontology_build_id,
            profile=profile,
            limit=CANDIDATE_POOL_SIZE,
        )
        long_term_ontology = retrieve_long_term_ontology_candidates(
            db,
            ontology_build_id=artifact.ontology_build_id,
            profile=profile,
            artifact=artifact,
            limit=CANDIDATE_POOL_SIZE,
            enforce_mature_cold_item_filter=(
                not args.disable_mature_cold_item_gate
            ),
        )
        confidence = assess_user_collaborative_confidence(
            positive_pair_count=profile.long_term.positive_pair_count,
            population_confidence=artifact.collaborative_population_confidence,
        )
        merged = merge_candidates(
            long_term_candidates,
            short_term.candidates,
            long_term_ontology.candidates,
            drift_confidence=profile.short_term.drift_confidence,
            collaborative_population_confidence=confidence.population_confidence,
            collaborative_user_evidence_confidence=confidence.user_evidence_confidence,
            collaborative_effective_confidence=confidence.effective_confidence,
            limit=CANDIDATE_STORAGE_SIZE,
        )
        eligibility = select_eligible_candidates(
            db,
            candidates=merged.candidates,
            profile=profile,
            context=context,
            movie_identity_supported=(
                None
                if args.disable_mature_cold_item_gate
                else artifact.movie_identity_supported
            ),
            limit=CANDIDATE_POOL_SIZE,
        )
        analyses = analyze_candidates(
            db,
            ontology_build_id=artifact.ontology_build_id,
            candidate_movie_ids=[item.movie_id for item in eligibility.candidates],
            profile=profile,
            artifact=artifact,
        )
        policy = evaluate_candidate_set(
            db,
            candidates=eligibility.candidates,
            analyses=analyses.candidates,
            profile=profile,
            context=context,
            prior_rejections=eligibility.rejections,
            input_candidate_count=len(merged.candidates),
        )
        db.rollback()

    holdout = {
        int(item["movie_id"]): rating_relevance(float(item["rating"]))
        for item in plan["holdout"]
        if rating_relevance(float(item["rating"])) > 0.0
    }
    final_ranking = [item.movie_id for item in policy.candidates]
    final_metrics = evaluate_ranking(final_ranking, holdout)
    all_ids = {
        item.movie_id for item in long_term_candidates
    } | {item.movie_id for item in long_term_ontology.candidates}
    all_ids.update(rejection.movie_id for rejection in initial_model.rejections)
    all_ids.update(rejection.movie_id for rejection in eligibility.rejections)
    metadata = enrich_metadata(
        load_db_metadata(all_ids),
        load_source_by_tmdb(ROOT / "inputs/links.csv"),
        load_movielens_movies(ROOT / "inputs/movies.csv"),
    )
    merged_by_id = {item.movie_id: item for item in merged.candidates}
    eligible_by_id = {item.movie_id: item for item in eligibility.candidates}
    final_by_id = {item.movie_id: item for item in policy.candidates}
    pre_rerank_rank_by_id = {
        item.movie_id: rank
        for rank, item in enumerate(
            sorted(
                policy.candidates,
                key=lambda value: (
                    -value.score.pre_rerank_score,
                    value.candidate.selection_rank,
                    value.movie_id,
                ),
            ),
            start=1,
        )
    }
    rejection_by_id = {
        item.movie_id: [reason.value for reason in item.reasons]
        for item in eligibility.rejections
    }
    model_rows = []
    for candidate in long_term_candidates:
        movie_id = candidate.movie_id
        model_rank = candidate.source_rank
        model_score = candidate.model_raw_score
        merged_item = merged_by_id.get(movie_id)
        eligible_item = eligible_by_id.get(movie_id)
        final_item = final_by_id.get(movie_id)
        if merged_item is None:
            loss_stage = "merge_top150"
        elif eligible_item is None:
            loss_stage = (
                "hard_filter" if movie_id in rejection_by_id else "eligibility_top100"
            )
        else:
            loss_stage = None
        model_rows.append(
            {
                "movie_id": movie_id,
                "title": metadata[movie_id]["title"],
                "model_rank": model_rank,
                "model_score": model_score,
                "holdout_rating": holdout.get(movie_id),
                "merged_rank": merged_item.selection_rank if merged_item else None,
                "candidate_selection_score": (
                    merged_item.candidate_selection_score if merged_item else None
                ),
                "eligible": eligible_item is not None,
                "rejection_reasons": rejection_by_id.get(movie_id, []),
                "final_rank": final_item.rank if final_item else None,
                "pre_rerank_rank": pre_rerank_rank_by_id.get(movie_id),
                "loss_stage": loss_stage,
                "policy_score": asdict(final_item.score) if final_item else None,
            }
        )

    payload = {
        "source_user_id": args.source_user_id,
        "user_id": int(plan["user_id"]),
        "model_build_id": artifact.model_build_id,
        "mature_cold_item_gate_enabled": not args.disable_mature_cold_item_gate,
        "initial_model_filter": {
            "input_candidate_count": len(raw_long_term_candidates),
            "inspected_candidate_count": initial_model.inspected_candidate_count,
            "selected_candidate_count": len(initial_model.candidates),
            "rejection_counts": list(initial_model.rejection_counts),
            "rejections": [
                {
                    "movie_id": rejection.movie_id,
                    "title": metadata[rejection.movie_id]["title"],
                    "release_date": metadata[rejection.movie_id]["release_date"],
                    "vote_count": metadata[rejection.movie_id]["vote_count"],
                    "reasons": [reason.value for reason in rejection.reasons],
                }
                for rejection in initial_model.rejections
            ],
        },
        "short_term_diagnostics": asdict(short_term.diagnostics),
        "long_term_ontology_diagnostics": asdict(long_term_ontology.diagnostics),
        "ontology_analysis_diagnostics": asdict(analyses.diagnostics),
        "final_metrics": {
            "ndcg": {str(key): value for key, value in final_metrics.ndcg.items()},
        },
        "merge_diagnostics": asdict(merged.diagnostics),
        "eligibility_diagnostics": asdict(eligibility.diagnostics),
        "eligibility_rejections": [
            {
                "movie_id": rejection.movie_id,
                "title": metadata[rejection.movie_id]["title"],
                "release_date": metadata[rejection.movie_id]["release_date"],
                "vote_count": metadata[rejection.movie_id]["vote_count"],
                "reasons": [reason.value for reason in rejection.reasons],
            }
            for rejection in eligibility.rejections
        ],
        "policy_diagnostics": asdict(policy.diagnostics),
        "model_top100_holdout": [
            row for row in model_rows[:100] if row["holdout_rating"] is not None
        ],
        "final_top100_holdout": [
            {
                "movie_id": item.movie_id,
                "title": metadata[item.movie_id]["title"],
                "final_rank": item.rank,
                "holdout_relevance": holdout[item.movie_id],
                "sources": [source.value for source in item.candidate.sources],
                "model_rank": item.candidate.model_source_rank,
                "ontology_rank": item.candidate.long_term_ontology_source_rank,
            }
            for item in policy.candidates
            if item.movie_id in holdout
        ],
        "model_top20": model_rows[:20],
        "ontology_top20": [
            {
                "movie_id": item.movie_id,
                "title": metadata[item.movie_id]["title"],
                "source_rank": item.source_rank,
                "raw_score": item.ontology_raw_score,
                "holdout_rating": holdout.get(item.movie_id),
                "vote_count": metadata[item.movie_id]["vote_count"],
                "vote_average": metadata[item.movie_id]["vote_average"],
                "genres": metadata[item.movie_id]["genres"],
            }
            for item in long_term_ontology.candidates[:20]
        ],
        "final_top20": [
            {
                "movie_id": item.movie_id,
                "title": metadata[item.movie_id]["title"],
                "holdout_rating": holdout.get(item.movie_id),
                "vote_count": metadata[item.movie_id]["vote_count"],
                "vote_average": metadata[item.movie_id]["vote_average"],
                "genres": metadata[item.movie_id]["genres"],
                "sources": [source.value for source in item.candidate.sources],
                "model_rank": item.candidate.model_source_rank,
                "ontology_rank": item.candidate.long_term_ontology_source_rank,
                "merged_rank": item.candidate.selection_rank,
                "pre_rerank_rank": pre_rerank_rank_by_id[item.movie_id],
                "score": asdict(item.score),
                "ontology_type_scores": [
                    asdict(type_score) for type_score in item.ontology.type_scores
                ],
            }
            for item in policy.candidates[:20]
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
