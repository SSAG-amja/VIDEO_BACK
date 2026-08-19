import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pandas as pd

from evaluation.benchmark import CASES_PATH, MANIFEST_PATH, load_cohorts


REQUIRED_FILES = (
    "ml-32m/filtered_links.csv",
    "ml-32m/filtered_ratings.csv",
    "ml-32m/test_ids.csv",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the fixed 500-user benchmark cases.")
    parser.add_argument("--dataset", type=Path, default=Path("../ml-32m.zip"))
    parser.add_argument("--output", type=Path, default=CASES_PATH)
    args = parser.parse_args()
    prepare_cases(args.dataset, args.output)


def prepare_cases(zip_path: Path, output_path: Path) -> None:
    cohorts = load_cohorts()
    user_ids = {user_id for values in cohorts.values() for user_id in values}
    with ZipFile(zip_path) as archive:
        missing = set(REQUIRED_FILES) - set(archive.namelist())
        if missing:
            raise ValueError(f"{zip_path}: missing files: {sorted(missing)}")
        movie_map = _load_movie_map(archive)
        train_counts = _load_train_counts(archive, user_ids)
        ratings = {user_id: [] for user_id in user_ids}
        with archive.open("ml-32m/filtered_ratings.csv") as source:
            for chunk in pd.read_csv(
                source,
                usecols=["userId", "movieId", "rating", "timestamp"],
                chunksize=1_000_000,
            ):
                selected = chunk[chunk["userId"].isin(user_ids)]
                for row in selected.itertuples(index=False):
                    mapped_id = movie_map.get(int(row.movieId))
                    if mapped_id is not None:
                        ratings[int(row.userId)].append(
                            [mapped_id, float(row.rating), int(row.timestamp)]
                        )

    missing_users = user_ids - {user_id for user_id, rows in ratings.items() if rows}
    if missing_users or user_ids - train_counts.keys():
        raise ValueError(f"fixed users missing from source data: {sorted(missing_users)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_rating_count = 0
    prepared_rating_count = 0
    movie_ids: set[int] = set()
    with gzip.open(output_path, "wt", encoding="utf-8", newline="\n") as output:
        for user_id in sorted(user_ids):
            ordered = sorted(enumerate(ratings[user_id]), key=lambda item: (item[1][2], item[0]))
            values = [row for _sequence, row in ordered]
            split_at = train_counts[user_id]
            if split_at <= 0 or split_at >= len(values):
                raise ValueError(f"user_id={user_id}: invalid train_count={split_at}")
            source_rating_count += len(values)
            train = _keep_latest_by_movie(values[:split_at])
            trained_movie_ids = {row[0] for row in train}
            test = [
                row
                for row in _keep_latest_by_movie(values[split_at:])
                if row[0] not in trained_movie_ids
            ]
            if not train or not test:
                raise ValueError(f"user_id={user_id}: empty split after movie deduplication")
            movie_ids.update(row[0] for row in [*train, *test])
            prepared_rating_count += len(train) + len(test)
            output.write(
                json.dumps(
                    {"user_id": user_id, "train": train, "test": test},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    manifest = {
        "dataset_version": "movielens-32m-fixed-v1",
        "generated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "source_archive": zip_path.name,
        "source_sha256": _sha256(zip_path),
        "users": len(user_ids),
        "source_ratings": source_rating_count,
        "prepared_ratings": prepared_rating_count,
        "movies": len(movie_ids),
        "split": "precomputed chronological 70/30 from test_ids.csv",
        "mapping": "filtered_links.csv videoBackMovieId",
        "duplicate_policy": "keep latest per split; exclude train movies from test",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)


def _load_movie_map(archive: ZipFile) -> dict[int, int]:
    with archive.open("ml-32m/filtered_links.csv") as source:
        rows = csv.DictReader(line.decode("utf-8") for line in source)
        return {int(row["movieId"]): int(row["videoBackMovieId"]) for row in rows}


def _load_train_counts(archive: ZipFile, user_ids: set[int]) -> dict[int, int]:
    with archive.open("ml-32m/test_ids.csv") as source:
        rows = csv.DictReader(line.decode("utf-8") for line in source)
        return {
            int(row["userId"]): int(row["train_count"])
            for row in rows
            if int(row["userId"]) in user_ids
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _keep_latest_by_movie(rows: list[list[int | float]]) -> list[list[int | float]]:
    seen: set[int] = set()
    kept_reversed: list[list[int | float]] = []
    for row in reversed(rows):
        movie_id = int(row[0])
        if movie_id not in seen:
            seen.add(movie_id)
            kept_reversed.append(row)
    return list(reversed(kept_reversed))


if __name__ == "__main__":
    main()
