from pathlib import Path

from app.crud.recsys.recommendations import load_eligible_users_and_exclusions
from app.db.session import SessionLocal
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact
from app.jobs.recsys.v3.candidates.candidate_schemas import CandidateMaterializationConfig, LoadedCandidateSnapshot
from app.jobs.recsys.v3.candidates.candidate_snapshot import materialize_candidate_snapshot


def run_worker(
    model_artifact: str | Path,
    *,
    output_root: str | Path = "assets/ml_models/v3/candidate_snapshots",
    config: CandidateMaterializationConfig | None = None,
) -> LoadedCandidateSnapshot:
    artifact = load_hybrid_artifact(model_artifact)
    with SessionLocal() as db:
        eligible_user_ids, exclusions = load_eligible_users_and_exclusions(db, artifact.user_ids)
        db.rollback()
    return materialize_candidate_snapshot(
        artifact,
        exclusions_by_user_id=exclusions,
        eligible_user_ids=eligible_user_ids,
        config=config,
        output_root=output_root,
    )


if __name__ == "__main__":
    from app.jobs.recsys.v3.candidates.materialize_candidates import main

    main()
