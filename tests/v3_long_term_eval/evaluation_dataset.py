from __future__ import annotations

from collections import Counter
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.recsys.v3.datasets.dataset_builder import calculate_dataset_hash
from app.jobs.recsys.v3.datasets.dataset_schemas import LightFMDatasetSnapshot
from app.models.user import User


def evaluation_user_ids(db: Session, plans: list[dict]) -> tuple[int, ...]:
    emails = [str(plan["email"]) for plan in plans]
    ids_by_email = dict(
        db.execute(select(User.email, User.id).where(User.email.in_(emails)))
        .tuples()
        .all()
    )
    missing = sorted(email for email in emails if email not in ids_by_email)
    if missing:
        raise ValueError(f"{len(missing)} evaluation users are missing from the database")
    return tuple(sorted(int(ids_by_email[email]) for email in emails))


def restrict_to_evaluation_users(
    dataset: LightFMDatasetSnapshot,
    *,
    user_ids: tuple[int, ...],
) -> LightFMDatasetSnapshot:
    if dataset.diagnostics is None:
        raise ValueError("evaluation dataset diagnostics are required")
    selected_ids = tuple(sorted(set(int(user_id) for user_id in user_ids)))
    missing = sorted(set(selected_ids) - set(dataset.user_ids))
    if missing:
        raise ValueError(
            f"{len(missing)} evaluation users have no positive training interactions"
        )

    row_indices = [dataset.user_id_map[user_id] for user_id in selected_ids]
    interactions = dataset.interactions.tocsr()[row_indices].tocoo()
    sample_weights = dataset.sample_weights.tocsr()[row_indices].tocoo()
    selected_set = frozenset(selected_ids)
    positives = tuple(
        positive for positive in dataset.positives if positive.user_id in selected_set
    )
    passed = {
        user_id: movie_ids
        for user_id, movie_ids in dataset.passed_movie_ids_by_user.items()
        if user_id in selected_set
    }
    watched = {
        user_id: movie_ids
        for user_id, movie_ids in dataset.watched_movie_ids_by_user.items()
        if user_id in selected_set
    }
    excluded = {
        user_id: movie_ids
        for user_id, movie_ids in dataset.excluded_movie_ids_by_user.items()
        if user_id in selected_set
    }
    dataset_hash = calculate_dataset_hash(
        data_cutoff_at=dataset.data_cutoff_at,
        movie_ids=dataset.movie_ids,
        positives=positives,
        passed_by_user=passed,
        watched_by_user=watched,
    )
    action_counts = Counter(
        action.value
        for positive in positives
        for action in positive.actions
    )
    action_counts["passed"] += sum(len(movie_ids) for movie_ids in passed.values())
    diagnostics = replace(
        dataset.diagnostics,
        model_user_count=len(selected_ids),
        positive_pair_count=len(positives),
        raw_signal_count=sum(action_counts.values()),
        action_signal_counts=dict(sorted(action_counts.items())),
        passed_movie_count=sum(len(movie_ids) for movie_ids in passed.values()),
        watched_movie_count=sum(len(movie_ids) for movie_ids in watched.values()),
        excluded_pair_count=sum(len(movie_ids) for movie_ids in excluded.values()),
        passed_positive_conflict_count=0,
        missing_timestamp_count=sum(
            positive.latest_at is None for positive in positives
        ),
        dataset_hash=dataset_hash,
        social_raw_signal_count=0,
        social_eligible_signal_count=0,
        social_action_signal_counts={},
        social_deferred_event_counts={},
        social_projection_hash="",
    )
    return replace(
        dataset,
        user_ids=selected_ids,
        user_id_map={user_id: index for index, user_id in enumerate(selected_ids)},
        interactions=interactions,
        sample_weights=sample_weights,
        positives=positives,
        social_signals=(),
        social_projection_diagnostics=None,
        excluded_movie_ids_by_user=excluded,
        passed_movie_ids_by_user=passed,
        watched_movie_ids_by_user=watched,
        diagnostics=diagnostics,
    )
