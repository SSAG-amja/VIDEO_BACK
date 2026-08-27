from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.mapping import MovieOtt
from app.services.recsys.v3.config import CANDIDATE_POOL_SIZE
from app.services.recsys.v3.domain.feature_registry import FeatureName, get_feature_definition
from app.services.recsys.v3.profiles.profile_builder import (
    build_onboarding_feature_signals,
    validate_profile_build,
)
from app.services.recsys.v3.retrieval.retrieval_schemas import (
    CandidateFeatureSet,
    CandidateOntologyAnalysis,
    OntologyAnalysisResult,
    OntologyAnalyzerDiagnostics,
    OntologyTypeScore,
    OttCandidateEvidence,
    ProfileScope,
)
from app.services.recsys.v3.domain.schemas import FeatureDirection, ProfileFeatureSignal, UserProfileBundle


ANALYZER_FEATURE_ORDER = (
    FeatureName.GENRE,
    FeatureName.KEYWORD,
    FeatureName.ACTOR,
    FeatureName.DIRECTOR,
    FeatureName.THEME,
    FeatureName.MOOD,
)

REPETITION_FEATURE_ORDER = (
    FeatureName.GENRE,
    FeatureName.ACTOR,
    FeatureName.DIRECTOR,
    FeatureName.THEME,
    FeatureName.MOOD,
)


def analyze_candidates(
    db: Session,
    *,
    ontology_build_id: int,
    candidate_movie_ids: Sequence[int],
    profile: UserProfileBundle,
    include_onboarding: bool = False,
) -> OntologyAnalysisResult:
    started = time.monotonic()
    movie_ids = _validate_candidate_ids(candidate_movie_ids)
    validate_profile_build(db, ontology_build_id)
    profile_rows = build_profile_rows(profile, include_onboarding=include_onboarding)
    aggregate_rows = load_candidate_type_aggregates(
        db,
        ontology_build_id=ontology_build_id,
        candidate_movie_ids=movie_ids,
        profile_rows=profile_rows,
    )
    repetition_rows = load_candidate_repetition_features(
        db,
        ontology_build_id=ontology_build_id,
        candidate_movie_ids=movie_ids,
    )
    ott_rows = load_candidate_streaming_otts(db, movie_ids)
    analyses = assemble_candidate_ontology_analyses(
        candidate_movie_ids=movie_ids,
        aggregate_rows=aggregate_rows,
        repetition_feature_rows=repetition_rows,
        streaming_ott_rows=ott_rows,
        subscribed_ott_ids=profile.serving_context.subscribed_ott_ids,
    )
    matched_movie_ids = {int(row[0]) for row in aggregate_rows}
    return OntologyAnalysisResult(
        candidates=analyses,
        diagnostics=OntologyAnalyzerDiagnostics(
            ontology_build_id=ontology_build_id,
            candidate_count=len(movie_ids),
            matched_candidate_count=len(matched_movie_ids),
            aggregate_row_count=len(aggregate_rows),
            repetition_feature_row_count=len(repetition_rows),
            streaming_ott_row_count=len(ott_rows),
            query_count=(
                1
                + int(bool(movie_ids and profile_rows))
                + int(bool(movie_ids))
                + int(bool(movie_ids))
            ),
            elapsed_seconds=round(time.monotonic() - started, 6),
        ),
    )


def build_profile_rows(
    profile: UserProfileBundle,
    *,
    include_onboarding: bool = False,
) -> tuple[tuple[str, str, str, str, str, str, float], ...]:
    rows: dict[tuple[str, str, str, str, str, str], float] = {}
    groups = [
        (ProfileScope.LONG_TERM, FeatureDirection.POSITIVE, profile.long_term.positive_features),
        (ProfileScope.LONG_TERM, FeatureDirection.NEGATIVE, profile.long_term.negative_features),
        (ProfileScope.SHORT_TERM, FeatureDirection.POSITIVE, profile.short_term.positive_features),
        (ProfileScope.SHORT_TERM, FeatureDirection.NEGATIVE, profile.short_term.negative_features),
    ]
    if include_onboarding:
        groups.insert(
            0,
            (
                ProfileScope.LONG_TERM,
                FeatureDirection.POSITIVE,
                build_onboarding_feature_signals(profile.onboarding),
            ),
        )
    for scope, direction, features in groups:
        for signal in features:
            relation_type, node_type = _profile_relation(signal)
            key = (
                relation_type,
                signal.feature.value,
                node_type,
                signal.ref_id,
                scope.value,
                direction.value,
            )
            rows[key] = max(rows.get(key, 0.0), float(signal.score))
    return tuple((*key, score) for key, score in sorted(rows.items()))


def load_candidate_type_aggregates(
    db: Session,
    *,
    ontology_build_id: int,
    candidate_movie_ids: tuple[int, ...],
    profile_rows: tuple[tuple[str, str, str, str, str, str, float], ...],
) -> list[tuple]:
    if not candidate_movie_ids or not profile_rows:
        return []
    relation_types, feature_names, node_types, ref_ids, scopes, directions, profile_scores = zip(
        *profile_rows,
        strict=True,
    )
    rows = db.execute(
        text(
            """
            WITH candidate_input(movie_id) AS (
                SELECT unnest(CAST(:candidate_movie_ids AS integer[]))
            ),
            candidate_nodes AS MATERIALIZED (
                SELECT candidate_input.movie_id,
                       node.id AS movie_node_id
                FROM candidate_input
                JOIN ontology_nodes node
                  ON node.build_id = :build_id
                 AND node.node_type = 'movie'
                 AND node.ref_id = candidate_input.movie_id::text
                 AND node.is_active IS TRUE
            ),
            profile_feature(
                relation_type, feature_name, node_type, ref_id, profile_scope,
                direction, profile_score
            ) AS (
                SELECT *
                FROM unnest(
                    CAST(:relation_types AS text[]),
                    CAST(:feature_names AS text[]),
                    CAST(:node_types AS text[]),
                    CAST(:ref_ids AS text[]),
                    CAST(:profile_scopes AS text[]),
                    CAST(:directions AS text[]),
                    CAST(:profile_scores AS double precision[])
                )
            ),
            profile_nodes AS MATERIALIZED (
                SELECT profile_feature.*,
                       node.id AS feature_node_id
                FROM profile_feature
                JOIN ontology_nodes node
                  ON node.build_id = :build_id
                 AND node.node_type = profile_feature.node_type
                 AND node.ref_id = profile_feature.ref_id
                 AND node.is_active IS TRUE
            )
            SELECT candidate_node.movie_id,
                   profile_node.feature_name,
                   profile_node.profile_scope,
                   profile_node.direction,
                   sum(
                       profile_node.profile_score
                       * COALESCE(edge.effective_strength, edge.weight * edge.confidence)
                   ) AS raw_score,
                   max(
                       profile_node.profile_score
                       * COALESCE(edge.effective_strength, edge.weight * edge.confidence)
                   ) AS peak_score,
                   count(*)::integer AS match_count
            FROM candidate_nodes candidate_node
            JOIN ontology_edges edge
              ON edge.build_id = :build_id
             AND edge.source_node_id = candidate_node.movie_node_id
            JOIN profile_nodes profile_node
              ON profile_node.feature_node_id = edge.target_node_id
             AND profile_node.relation_type = edge.relation_type
            GROUP BY candidate_node.movie_id,
                     profile_node.feature_name,
                     profile_node.profile_scope,
                     profile_node.direction
            ORDER BY candidate_node.movie_id,
                     profile_node.feature_name,
                     profile_node.profile_scope,
                     profile_node.direction
            """
        ),
        {
            "build_id": ontology_build_id,
            "candidate_movie_ids": list(candidate_movie_ids),
            "relation_types": list(relation_types),
            "feature_names": list(feature_names),
            "node_types": list(node_types),
            "ref_ids": list(ref_ids),
            "profile_scopes": list(scopes),
            "directions": list(directions),
            "profile_scores": list(profile_scores),
        },
    )
    return list(rows)


def load_candidate_streaming_otts(
    db: Session,
    candidate_movie_ids: tuple[int, ...],
) -> list[tuple[int, int]]:
    if not candidate_movie_ids:
        return []
    return [
        (int(movie_id), int(ott_id))
        for movie_id, ott_id in db.execute(
            select(MovieOtt.movie_id, MovieOtt.ott_id)
            .where(
                MovieOtt.movie_id.in_(candidate_movie_ids),
                MovieOtt.is_streaming.is_(True),
            )
            .order_by(MovieOtt.movie_id, MovieOtt.ott_id)
        )
    ]


def load_candidate_repetition_features(
    db: Session,
    *,
    ontology_build_id: int,
    candidate_movie_ids: tuple[int, ...],
) -> list[tuple[int, str, str]]:
    if not candidate_movie_ids:
        return []
    feature_relations = tuple(
        (
            get_feature_definition(feature).ontology_relations[0],
            feature.value,
        )
        for feature in REPETITION_FEATURE_ORDER
    )
    relation_types, feature_names = zip(*feature_relations, strict=True)
    rows = db.execute(
        text(
            """
            WITH candidate_input(movie_id) AS (
                SELECT unnest(CAST(:candidate_movie_ids AS integer[]))
            ),
            candidate_nodes AS MATERIALIZED (
                SELECT candidate_input.movie_id, node.id AS movie_node_id
                FROM candidate_input
                JOIN ontology_nodes node
                  ON node.build_id = :build_id
                 AND node.node_type = 'movie'
                 AND node.ref_id = candidate_input.movie_id::text
                 AND node.is_active IS TRUE
            ),
            repetition_relation(relation_type, feature_name) AS (
                SELECT *
                FROM unnest(
                    CAST(:relation_types AS text[]),
                    CAST(:feature_names AS text[])
                )
            )
            SELECT candidate_node.movie_id,
                   repetition_relation.feature_name,
                   feature_node.ref_id
            FROM candidate_nodes candidate_node
            JOIN ontology_edges edge
              ON edge.build_id = :build_id
             AND edge.source_node_id = candidate_node.movie_node_id
            JOIN repetition_relation
              ON repetition_relation.relation_type = edge.relation_type
            JOIN ontology_nodes feature_node
              ON feature_node.id = edge.target_node_id
             AND feature_node.build_id = :build_id
             AND feature_node.is_active IS TRUE
            ORDER BY candidate_node.movie_id,
                     repetition_relation.feature_name,
                     feature_node.ref_id
            """
        ),
        {
            "build_id": ontology_build_id,
            "candidate_movie_ids": list(candidate_movie_ids),
            "relation_types": list(relation_types),
            "feature_names": list(feature_names),
        },
    )
    return [(int(movie_id), str(feature), str(ref_id)) for movie_id, feature, ref_id in rows]


def assemble_candidate_ontology_analyses(
    *,
    candidate_movie_ids: Sequence[int],
    aggregate_rows: Iterable[tuple],
    streaming_ott_rows: Iterable[tuple[int, int]],
    subscribed_ott_ids: frozenset[int],
    repetition_feature_rows: Iterable[tuple[int, str, str]] = (),
) -> tuple[CandidateOntologyAnalysis, ...]:
    values: dict[tuple[int, FeatureName], dict[tuple[ProfileScope, FeatureDirection], tuple[float, int]]] = {}
    for movie_id, feature_name, scope, direction, raw_score, peak_score, match_count in aggregate_rows:
        feature = FeatureName(str(feature_name))
        damped_score = _damped_type_score(float(raw_score), float(peak_score))
        values.setdefault((int(movie_id), feature), {})[
            (ProfileScope(str(scope)), FeatureDirection(str(direction)))
        ] = (damped_score, int(match_count))

    streaming_by_movie: dict[int, set[int]] = defaultdict(set)
    for movie_id, ott_id in streaming_ott_rows:
        streaming_by_movie[int(movie_id)].add(int(ott_id))

    repetition_by_movie: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for movie_id, feature_name, ref_id in repetition_feature_rows:
        repetition_by_movie[int(movie_id)][str(feature_name)].add(str(ref_id))

    analyses: list[CandidateOntologyAnalysis] = []
    for movie_id in candidate_movie_ids:
        type_scores: list[OntologyTypeScore] = []
        for feature in ANALYZER_FEATURE_ORDER:
            scoped = values.get((int(movie_id), feature), {})
            long_positive = scoped.get((ProfileScope.LONG_TERM, FeatureDirection.POSITIVE), (0.0, 0))
            long_negative = scoped.get((ProfileScope.LONG_TERM, FeatureDirection.NEGATIVE), (0.0, 0))
            short_positive = scoped.get((ProfileScope.SHORT_TERM, FeatureDirection.POSITIVE), (0.0, 0))
            short_negative = scoped.get((ProfileScope.SHORT_TERM, FeatureDirection.NEGATIVE), (0.0, 0))
            type_scores.append(
                OntologyTypeScore(
                    feature=feature,
                    long_positive_score=long_positive[0],
                    long_negative_score=long_negative[0],
                    short_positive_score=short_positive[0],
                    short_negative_score=short_negative[0],
                    long_positive_match_count=long_positive[1],
                    long_negative_match_count=long_negative[1],
                    short_positive_match_count=short_positive[1],
                    short_negative_match_count=short_negative[1],
                )
            )
        streaming_ids = frozenset(streaming_by_movie.get(int(movie_id), ()))
        analyses.append(
            CandidateOntologyAnalysis(
                movie_id=int(movie_id),
                type_scores=tuple(type_scores),
                long_positive_total=sum(item.long_positive_score for item in type_scores),
                long_negative_total=sum(item.long_negative_score for item in type_scores),
                short_positive_total=sum(item.short_positive_score for item in type_scores),
                short_negative_total=sum(item.short_negative_score for item in type_scores),
                ott=OttCandidateEvidence(
                    streaming_ott_ids=streaming_ids,
                    subscribed_streaming_ott_ids=streaming_ids & subscribed_ott_ids,
                ),
                repetition_features=CandidateFeatureSet(
                    genre=frozenset(repetition_by_movie[int(movie_id)][FeatureName.GENRE.value]),
                    actor=frozenset(repetition_by_movie[int(movie_id)][FeatureName.ACTOR.value]),
                    director=frozenset(
                        repetition_by_movie[int(movie_id)][FeatureName.DIRECTOR.value]
                    ),
                    theme=frozenset(repetition_by_movie[int(movie_id)][FeatureName.THEME.value]),
                    mood=frozenset(repetition_by_movie[int(movie_id)][FeatureName.MOOD.value]),
                ),
            )
        )
    return tuple(analyses)


def _profile_relation(signal: ProfileFeatureSignal) -> tuple[str, str]:
    definition = get_feature_definition(signal.feature)
    relations = definition.ontology_relations
    if len(relations) != 1:
        raise ValueError(f"profile feature requires one ontology relation feature={signal.feature.value}")
    if definition.ontology_node_type is None:
        raise ValueError(f"profile feature requires an ontology node type feature={signal.feature.value}")
    return relations[0], definition.ontology_node_type


def _damped_type_score(raw_score: float, peak_score: float) -> float:
    if not math.isfinite(raw_score) or not math.isfinite(peak_score):
        raise ValueError("ontology aggregate scores must be finite")
    if raw_score <= 0.0 or peak_score <= 0.0:
        return 0.0
    return round(peak_score * (1.0 + math.log(max(raw_score / peak_score, 1.0))), 8)


def _validate_candidate_ids(candidate_movie_ids: Sequence[int]) -> tuple[int, ...]:
    movie_ids = tuple(int(value) for value in candidate_movie_ids)
    if len(movie_ids) > CANDIDATE_POOL_SIZE:
        raise ValueError(f"ontology analyzer accepts at most {CANDIDATE_POOL_SIZE} candidates")
    if any(value <= 0 for value in movie_ids):
        raise ValueError("ontology analyzer movie IDs must be positive")
    if len(set(movie_ids)) != len(movie_ids):
        raise ValueError("ontology analyzer movie IDs must be unique")
    return movie_ids
