import gzip
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Callable, Sequence

from evaluation.contracts import EvaluationEngine, Interaction, RecommendationInput


SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")
COHORTS_PATH = Path(__file__).with_name("cohorts.json")
CASES_PATH = Path(__file__).with_name("data") / "fixed_cases.jsonl.gz"
MANIFEST_PATH = Path(__file__).with_name("data") / "fixed_cases_manifest.json"


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    input_data: RecommendationInput
    ground_truth: dict[int, float]


def load_cohorts(path: Path = COHORTS_PATH) -> dict[str, list[int]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cohorts = raw.get("cohorts")
    if not isinstance(cohorts, dict) or not cohorts:
        raise ValueError(f"{path}: non-empty 'cohorts' object is required")
    normalized: dict[str, list[int]] = {}
    for name, values in cohorts.items():
        ids = [int(value) for value in values]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"{path}: cohort {name!r} is empty or has duplicate users")
        if str(len(ids)) != str(name):
            raise ValueError(f"{path}: cohort {name!r} contains {len(ids)} users")
        normalized[str(name)] = ids
    return dict(sorted(normalized.items(), key=lambda item: int(item[0])))


def load_cases(path: Path = CASES_PATH) -> dict[int, EvaluationCase]:
    if not path.is_file():
        raise FileNotFoundError(
            f"fixed benchmark cases not found: {path}; run python -m evaluation.prepare_cases"
        )
    cases: dict[int, EvaluationCase] = {}
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            user_id = int(row["user_id"])
            if user_id in cases:
                raise ValueError(f"{path}:{line_number}: duplicate user_id={user_id}")
            train = tuple(Interaction(*values) for values in row["train"])
            test = [Interaction(*values) for values in row["test"]]
            if not train or not test:
                raise ValueError(f"{path}:{line_number}: empty train or test split")
            ground_truth = {item.movie_id: item.rating for item in test}
            if len(ground_truth) != len(test):
                raise ValueError(f"{path}:{line_number}: duplicate evaluation movie IDs")
            cases[user_id] = EvaluationCase(
                input_data=RecommendationInput(
                    user_id=user_id,
                    training_interactions=train,
                    candidate_movie_ids=tuple(ground_truth),
                ),
                ground_truth=ground_truth,
            )
    return cases


def run_benchmark(
    *,
    test_name: str,
    cases_path: Path,
    output_root: Path,
    engine_factory: Callable[[], EvaluationEngine],
) -> Path:
    _validate_test_name(test_name)
    cohorts = load_cohorts()
    all_user_ids = {user_id for values in cohorts.values() for user_id in values}
    cases = load_cases(cases_path)
    missing_users = all_user_ids - cases.keys()
    if missing_users:
        raise ValueError(f"fixed benchmark cases are missing users: {sorted(missing_users)}")
    started_at = datetime.now(SEOUL)
    started_clock = time.perf_counter()
    cohort_results: dict[str, dict] = {}
    engine_metadata: dict | None = None

    for cohort_name, user_ids in cohorts.items():
        engine = engine_factory()
        current_metadata = {
            "name": str(engine.name),
            "version": str(engine.version),
        }
        metadata_provider = getattr(engine, "metadata", None)
        if callable(metadata_provider):
            current_metadata.update(metadata_provider())
        if engine_metadata is None:
            engine_metadata = current_metadata
        elif engine_metadata != current_metadata:
            raise ValueError("engine name/version changed between cohorts")

        cohort_cases = [cases[user_id] for user_id in user_ids]
        engine.prepare([case.input_data for case in cohort_cases])
        per_user = []
        for case in cohort_cases:
            ranking = engine.rank_candidates(case.input_data)
            metrics = evaluate_ranking(ranking, case.ground_truth)
            per_user.append({"user_id": case.input_data.user_id, **metrics})
        cohort_results[cohort_name] = {
            "user_ids": user_ids,
            "summary": summarize(per_user),
            "per_user": per_user,
        }

    finished_at = datetime.now(SEOUL)
    payload = {
        "test_name": test_name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.perf_counter() - started_clock, 6),
        "engine": engine_metadata,
        "dataset": _load_manifest(cases_path),
        "cohorts": cohort_results,
    }
    run_dir = output_root / test_name
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / f"{started_at.strftime('%Y%m%d_%H%M%S_%f')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def evaluate_ranking(ranking: Sequence[int], truth: dict[int, float]) -> dict[str, float | int]:
    known = set(truth)
    ranked: list[int] = []
    seen: set[int] = set()
    for raw_movie_id in ranking:
        movie_id = int(raw_movie_id)
        if movie_id in known and movie_id not in seen:
            ranked.append(movie_id)
            seen.add(movie_id)
    k = max(1, math.ceil(len(truth) * 0.20))
    ndcg_value = _ndcg(ranked, truth, k)
    recall_value = _recall(ranked, truth, k)
    return {
        "candidate_count": len(truth),
        "returned_candidate_count": len(seen),
        "coverage": len(seen) / len(truth) if truth else 0.0,
        "k_at_20_percent": k,
        "ndcg_at_20_percent": ndcg_value,
        "recall_at_20_percent": recall_value,
        "final_score": 0.8 * ndcg_value + 0.2 * recall_value,
    }


def summarize(rows: Sequence[dict]) -> dict:
    metric_names = (
        "coverage",
        "ndcg_at_20_percent",
        "recall_at_20_percent",
        "final_score",
    )
    result = {"users": len(rows), "metrics": {}}
    for name in metric_names:
        values = [float(row[name]) for row in rows]
        result["metrics"][name] = {
            "mean": fmean(values) if values else 0.0,
            "median": median(values) if values else 0.0,
            "stddev": pstdev(values) if values else 0.0,
        }
    return result


def _ndcg(ranked: Sequence[int], truth: dict[int, float], k: int) -> float:
    actual = [_relevance(truth[movie_id]) for movie_id in ranked[:k]]
    ideal = sorted((_relevance(value) for value in truth.values()), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    return _dcg(actual) / ideal_dcg if ideal_dcg else 0.0


def _recall(ranked: Sequence[int], truth: dict[int, float], k: int) -> float:
    relevant = {movie_id for movie_id, rating in truth.items() if rating >= 3.5}
    return sum(movie_id in relevant for movie_id in ranked[:k]) / len(relevant) if relevant else 0.0


def _relevance(rating: float) -> int:
    return 0 if rating < 3.5 else int(round((rating - 3.0) * 2))


def _dcg(values: Sequence[int]) -> float:
    return sum((2**value - 1) / math.log2(rank + 1) for rank, value in enumerate(values, 1))


def _load_manifest(cases_path: Path) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {**manifest, "cases_path": str(cases_path.resolve()), "cohort_config": COHORTS_PATH.name}


def _validate_test_name(value: str) -> None:
    if not value or value.strip() != value or len(value) > 100:
        raise ValueError("test name must be 1-100 characters without surrounding spaces")
    if value in {".", ".."} or any(character in value for character in '<>:"/\\|?*'):
        raise ValueError("test name contains characters that are unsafe in a Windows path")
