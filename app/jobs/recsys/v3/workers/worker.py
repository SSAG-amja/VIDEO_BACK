from pathlib import Path
from datetime import datetime, timezone

from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.db.session import SessionLocal
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig, LoadedCandidateSnapshot
from app.jobs.recsys.v3.candidates.candidate_snapshot import materialize_candidate_snapshot
from app.jobs.recsys.v3.candidates.candidate_confidence import (
    load_training_collaborative_confidences,
)
from app.services.recsys.v3.retrieval.initial_candidate_filter import (
    build_initial_catalog_mask,
    identity_supported_movie_ids,
)


def run_worker(
    model_artifact: str | Path,
    *,
    output_root: str | Path = "assets/ml_models/v3/candidate_snapshots",
    config: CandidateMaterializationConfig | None = None,
) -> LoadedCandidateSnapshot:
    artifact = load_hybrid_artifact(model_artifact)
    with SessionLocal() as db:
        eligible_user_ids, exclusions = load_eligible_users_and_exclusions(db, artifact.user_ids)
        collaborative_confidences = load_training_collaborative_confidences(db, artifact)
        initial_catalog = build_initial_catalog_mask(
            db,
            movie_ids=artifact.movie_ids,
            identity_supported_movie_ids=identity_supported_movie_ids(
                movie_ids=artifact.movie_ids,
                item_features=artifact.item_features,
            ),
            as_of=datetime.now(timezone.utc),
        )
        db.rollback()
    return materialize_candidate_snapshot(
        artifact,
        exclusions_by_user_id=exclusions,
        collaborative_confidence_by_user_id=collaborative_confidences,
        eligible_user_ids=eligible_user_ids,
        initial_eligible_item_mask=initial_catalog.eligible_item_mask,
        config=config,
        output_root=output_root,
    )


if __name__ == "__main__":
    from app.jobs.recsys.v3.candidates.materialize_candidates import main

    main()
