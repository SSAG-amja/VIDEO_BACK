import gzip
import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Callable, Sequence

from evaluation.contracts import EvaluationEngine, Interaction, RecommendationInput
from evaluation.datasets import resolve_movie_identities
from evaluation.provenance import collect_runtime_provenance, sha256_file


SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")
COHORTS_PATH = Path(__file__).with_name("cohorts.json")
CASES_PATH = Path(__file__).with_name("data") / "fixed_cases.jsonl.gz"
MANIFEST_PATH = Path(__file__).with_name("data") / "fixed_cases_manifest.json"
MOVIE_IDENTITIES_PATH = Path(__file__).with_name("data") / "fixed_movie_identities.json.gz"


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
    cohorts_path: Path = COHORTS_PATH,
    manifest_path: Path = MANIFEST_PATH,
    movie_identities_path: Path = MOVIE_IDENTITIES_PATH,
) -> Path:
    _validate_test_name(test_name)
    cohorts = load_cohorts(cohorts_path)
    all_user_ids = {user_id for values in cohorts.values() for user_id in values}
    cases = load_cases(cases_path)
    missing_users = all_user_ids - cases.keys()
    if missing_users:
        raise ValueError(f"fixed benchmark cases are missing users: {sorted(missing_users)}")
    dataset_metadata = _load_manifest(
        cases_path,
        cohorts_path,
        manifest_path,
        movie_identities_path,
    )
    started_at = datetime.now(SEOUL)
    started_clock = time.perf_counter()
    selected_cases = [cases[user_id] for user_id in all_user_ids]
    snapshot_movie_ids = {
        interaction.movie_id
        for case in selected_cases
        for interaction in case.input_data.training_interactions
    }
    snapshot_movie_ids.update(
        movie_id for case in selected_cases for movie_id in case.input_data.candidate_movie_ids
    )
    identity_resolution = resolve_movie_identities(movie_identities_path, snapshot_movie_ids)
    cases = _remap_cases(cases, identity_resolution.movie_id_map)
    selected_cases = [cases[user_id] for user_id in all_user_ids]
    evaluation_movie_ids = set(identity_resolution.movie_id_map.values())
    runtime_provenance = collect_runtime_provenance(evaluation_movie_ids)
    runtime_provenance["movie_identity_resolution"] = identity_resolution.metadata
    cohort_results: dict[str, dict] = {}
    engine_metadata: dict | None = None

    for cohort_name, user_ids in cohorts.items():
        engine = engine_factory()
        try:
            cohort_cases = [cases[user_id] for user_id in user_ids]
            engine.prepare([case.input_data for case in cohort_cases])
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
            cohort_clock = time.perf_counter()
            print(f"[{cohort_name}] evaluating {len(user_ids)} users", flush=True)
            per_user = []
            progress_interval = max(1, len(cohort_cases) // 10)
            def evaluate_case(case: EvaluationCase) -> dict:
                ranking = engine.rank(case.input_data)
                return {
                    "user_id": case.input_data.user_id,
                    **evaluate_ranking(ranking, case.ground_truth),
                }

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="evaluation") as executor:
                futures = [executor.submit(evaluate_case, case) for case in cohort_cases]
                for index, future in enumerate(as_completed(futures), start=1):
                    per_user.append(future.result())
                    if index == 1 or index == len(cohort_cases) or index % progress_interval == 0:
                        elapsed = time.perf_counter() - cohort_clock
                        rate = index / elapsed if elapsed > 0 else 0.0
                        remaining = (len(cohort_cases) - index) / rate if rate > 0 else 0.0
                        print(
                            f"[{cohort_name}] {index}/{len(cohort_cases)} "
                            f"elapsed={elapsed:.1f}s eta={remaining:.1f}s",
                            flush=True,
                        )
            per_user.sort(key=lambda row: row["user_id"])
            cohort_results[cohort_name] = {
                "user_ids": user_ids,
                "summary": summarize(per_user),
                "per_user": per_user,
            }
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    finished_at = datetime.now(SEOUL)
    payload = {
        "test_name": test_name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.perf_counter() - started_clock, 6),
        "engine": engine_metadata,
        "dataset": dataset_metadata,
        "runtime": runtime_provenance,
        "cohorts": cohort_results,
    }
    run_dir = output_root / test_name
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / f"{started_at.strftime('%Y%m%d_%H%M%S_%f')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_csv(output_path, payload)
    return output_path


def summary_path_for(output_path: Path) -> Path:
    return output_path.with_suffix(".summary.csv")


def write_summary_csv(output_path: Path, payload: dict) -> None:
    metric_names = ("coverage", "ndcg_at_20_percent", "recall_at_20_percent", "final_score")
    fieldnames = [
        "test_name", "engine", "engine_version", "started_at", "finished_at",
        "elapsed_seconds", "cohort", "users", *(f"{name}_mean" for name in metric_names),
    ]
    with summary_path_for(output_path).open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for cohort_name, cohort in payload["cohorts"].items():
            writer.writerow({
                "test_name": payload["test_name"],
                "engine": payload["engine"]["name"],
                "engine_version": payload["engine"]["version"],
                "started_at": payload["started_at"],
                "finished_at": payload["finished_at"],
                "elapsed_seconds": payload["elapsed_seconds"],
                "cohort": cohort_name,
                "users": cohort["summary"]["users"],
                **{f"{name}_mean": cohort["summary"]["metrics"][name]["mean"] for name in metric_names},
            })


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


def _load_manifest(
    cases_path: Path,
    cohorts_path: Path,
    manifest_path: Path,
    movie_identities_path: Path,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases_sha256 = sha256_file(cases_path)
    cohorts_sha256 = sha256_file(cohorts_path)
    movie_identities_sha256 = sha256_file(movie_identities_path)
    expected_cases_sha256 = manifest.get("cases_sha256")
    expected_cohorts_sha256 = manifest.get("cohorts_sha256")
    expected_movie_identities_sha256 = manifest.get("movie_identities_sha256")
    if not expected_cases_sha256 or not expected_cohorts_sha256 or not expected_movie_identities_sha256:
        raise ValueError(f"dataset manifest is missing required SHA-256 values: {manifest_path}")
    if expected_cases_sha256 and cases_sha256 != expected_cases_sha256:
        raise ValueError(
            f"fixed cases hash mismatch: expected={expected_cases_sha256} actual={cases_sha256}"
        )
    if expected_cohorts_sha256 and cohorts_sha256 != expected_cohorts_sha256:
        raise ValueError(
            f"cohort config hash mismatch: expected={expected_cohorts_sha256} actual={cohorts_sha256}"
        )
    if movie_identities_sha256 != expected_movie_identities_sha256:
        raise ValueError(
            "movie identities hash mismatch: "
            f"expected={expected_movie_identities_sha256} actual={movie_identities_sha256}"
        )
    return {
        **manifest,
        "cases_path": str(cases_path.resolve()),
        "cases_sha256": cases_sha256,
        "cohorts_path": str(cohorts_path.resolve()),
        "cohorts_sha256": cohorts_sha256,
        "manifest_path": str(manifest_path.resolve()),
        "movie_identities_path": str(movie_identities_path.resolve()),
        "movie_identities_sha256": movie_identities_sha256,
        "canonical_fixed_artifact_verified": True,
    }


def _remap_cases(
    cases: dict[int, EvaluationCase],
    movie_id_map: dict[int, int],
) -> dict[int, EvaluationCase]:
    remapped: dict[int, EvaluationCase] = {}
    for user_id, case in cases.items():
        training_interactions = tuple(
            Interaction(
                movie_id=movie_id_map[interaction.movie_id],
                rating=interaction.rating,
                timestamp=interaction.timestamp,
            )
            for interaction in case.input_data.training_interactions
        )
        ground_truth = {
            movie_id_map[movie_id]: rating
            for movie_id, rating in case.ground_truth.items()
        }
        if len(ground_truth) != len(case.ground_truth):
            raise ValueError(f"user_id={user_id}: movie identity remapping merged holdout movies")
        remapped[user_id] = EvaluationCase(
            input_data=RecommendationInput(
                user_id=user_id,
                training_interactions=training_interactions,
                candidate_movie_ids=tuple(ground_truth),
            ),
            ground_truth=ground_truth,
        )
    return remapped


def _validate_test_name(value: str) -> None:
    if not value or value.strip() != value or len(value) > 100:
        raise ValueError("test name must be 1-100 characters without surrounding spaces")
    if value in {".", ".."} or any(character in value for character in '<>:"/\\|?*'):
        raise ValueError("test name contains characters that are unsafe in a Windows path")
