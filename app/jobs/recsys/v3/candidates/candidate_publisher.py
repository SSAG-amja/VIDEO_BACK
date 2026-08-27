from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.crud.recsys.recommendations import (
    load_eligible_users_and_exclusions,
    replace_precomputed_candidate_rows,
)
from app.jobs.recsys.v3.candidates.candidate_schemas import LoadedCandidateSnapshot
from app.jobs.recsys.v3.candidates.candidate_snapshot import (
    hash_eligible_user_ids,
    hash_exclusions,
    iter_candidate_snapshot_batches,
)


def publish_candidate_snapshot(
    db: Session,
    snapshot: LoadedCandidateSnapshot,
    *,
    statement_chunk_size: int = 5_000,
) -> dict[str, int]:
    replaced_users = 0
    inserted_candidates = 0
    seen_users: set[int] = set()
    for batch in iter_candidate_snapshot_batches(snapshot):
        successful_user_ids = [int(value) for value in batch.successful_user_ids]
        duplicates = seen_users.intersection(successful_user_ids)
        if duplicates:
            raise ValueError(f"candidate snapshot repeats successful users: {sorted(duplicates)[:5]}")
        seen_users.update(successful_user_ids)
        replaced_users += len(successful_user_ids)

        rows = [
            {
                "user_id": int(user_id),
                "movie_id": int(movie_id),
                "score": float(score),
                "rank": int(rank),
                "source": "lightfm_v3",
                "source_scores": {
                    "model_raw_score": float(score),
                    "model_source_rank": int(rank),
                    "model_build_id": snapshot.model_build_id,
                    "candidate_snapshot_id": snapshot.snapshot_id,
                },
            }
            for user_id, movie_id, score, rank in zip(
                batch.candidate_user_ids,
                batch.movie_ids,
                batch.model_scores,
                batch.source_ranks,
                strict=True,
            )
        ]
        replace_precomputed_candidate_rows(
            db,
            user_ids=successful_user_ids,
            rows=rows,
            statement_chunk_size=statement_chunk_size,
        )
        inserted_candidates += len(rows)
    db.flush()
    return {
        "replaced_user_count": replaced_users,
        "inserted_candidate_count": inserted_candidates,
        "preserved_failed_user_count": int(snapshot.manifest["failed_user_count"]),
    }


def validate_snapshot_publication_state(
    db: Session,
    snapshot: LoadedCandidateSnapshot,
    artifact_user_ids: Sequence[int],
) -> tuple[tuple[int, ...], dict[int, set[int]]]:
    eligible_user_ids, exclusions = load_eligible_users_and_exclusions(db, artifact_user_ids)
    if hash_eligible_user_ids(eligible_user_ids) != snapshot.manifest["eligible_user_ids_hash"]:
        raise RuntimeError("eligible users changed after candidate materialization")
    if hash_exclusions(exclusions, eligible_user_ids) != snapshot.manifest["exclusion_hash"]:
        raise RuntimeError("watched/passed exclusions changed after candidate materialization")
    return eligible_user_ids, exclusions
