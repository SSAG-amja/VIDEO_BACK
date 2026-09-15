from __future__ import annotations

import logging
import uuid
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.crud.recsys.ontology_recommendations import add_recommendation_snapshots
from app.crud.recsys.recommendation_runs import create_run, mark_run_finished
from app.models.ontology_recommendations import OntologyRecommendation
from app.services.recsys.v4.policy.policy_config import policy_config_snapshot
from app.services.recsys.v4.policy.policy_schemas import (
    PolicyEvaluationResult,
    RankedPolicyCandidate,
)
from app.services.recsys.v4.retrieval.retrieval_schemas import CandidateSource
from app.services.recsys.v4.serving.serving_bundle import ServingBundle
from app.services.recsys.v4.config import ENGINE_NAME, ENGINE_VERSION


logger = logging.getLogger(__name__)


def persist_request_diagnostics(
    db: Session,
    *,
    bundle: ServingBundle,
    user_id: int,
    shuffle_seed: str | None,
    policy: PolicyEvaluationResult,
    page: tuple[RankedPolicyCandidate, ...],
    request_path: dict[str, object],
) -> None:
    request_id = uuid.uuid4().hex
    try:
        run = create_run(
            db,
            run_id=request_id,
            engine=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            ontology_build_id=bundle.ontology_build_id,
            run_type="request",
            config_snapshot={
                "request_marker": shuffle_seed,
                "bundle_id": bundle.bundle_id,
                "model_build_id": bundle.model.model_build_id,
                "candidate_snapshot_id": bundle.candidate_snapshot_id,
                "policy": policy_config_snapshot(),
                "request_path": request_path,
                "policy_diagnostics": asdict(policy.diagnostics),
                "policy_rejections": [
                    {
                        "movie_id": rejection.movie_id,
                        "reasons": [reason.value for reason in rejection.reasons],
                    }
                    for rejection in policy.rejections
                ],
            },
        )
        rows = _diagnostic_rows(
            request_id=request_id,
            feed_session_key=shuffle_seed,
            user_id=user_id,
            candidates=policy.candidates,
            stage="candidate_slice",
            bundle=bundle,
        )
        rows.extend(
            _diagnostic_rows(
                request_id=request_id,
                feed_session_key=shuffle_seed,
                user_id=user_id,
                candidates=page,
                stage="final_response",
                bundle=bundle,
            )
        )
        add_recommendation_snapshots(db, rows)
        source_counts: dict[str, int] = {}
        for candidate in policy.candidates:
            source = "+".join(item.value for item in candidate.candidate.sources)
            source_counts[source] = source_counts.get(source, 0) + 1
        mark_run_finished(
            db,
            run,
            status="success",
            processed_user_count=1,
            generated_candidate_count=len(policy.candidates),
            source_counts=source_counts,
            fallback_ratio=(
                sum(
                    CandidateSource.COLD_START in item.candidate.sources
                    for item in policy.candidates
                )
                / len(policy.candidates)
                if policy.candidates
                else 1.0
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("V4 recommendation diagnostics persistence failed", exc_info=True)


def _diagnostic_rows(
    *,
    request_id: str,
    feed_session_key: str | None,
    user_id: int,
    candidates: tuple[RankedPolicyCandidate, ...],
    stage: str,
    bundle: ServingBundle,
) -> list[OntologyRecommendation]:
    return [
        OntologyRecommendation(
            run_id=request_id,
            request_id=request_id,
            feed_session_key=feed_session_key,
            user_id=user_id,
            movie_id=item.movie_id,
            rank=item.rank,
            score=item.score.final_score,
            source="+".join(source.value for source in item.candidate.sources),
            source_scores={
                "serving_bundle_id": bundle.bundle_id,
                "model_build_id": bundle.model.model_build_id,
                "candidate_snapshot_id": bundle.candidate_snapshot_id,
                "score_trace": asdict(item.score),
                "ontology_type_scores": {
                    score.feature.value: {
                        "long_positive": score.long_positive_score,
                        "long_negative": score.long_negative_score,
                        "short_positive": score.short_positive_score,
                        "short_negative": score.short_negative_score,
                    }
                    for score in item.ontology.type_scores
                },
                "candidate_sources": [
                    source.value for source in item.candidate.sources
                ],
            },
            explanation_tags=[
                {
                    "reason_type": reason.reason_type.value,
                    "feature": reason.feature.value if reason.feature else None,
                    "value": reason.value,
                    "ref_ids": list(reason.ref_ids),
                    "is_model_attribution": reason.is_model_attribution,
                }
                for reason in item.reasons
            ],
            ontology_build_id=bundle.ontology_build_id,
            engine_version=ENGINE_VERSION,
            candidate_stage=stage,
        )
        for item in candidates
    ]
