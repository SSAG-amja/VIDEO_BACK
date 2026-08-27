import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.movie import Movie


EVALUATION_ROOT = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class DatasetPaths:
    cases: Path
    cohorts: Path
    manifest: Path
    movie_identities: Path


@dataclass(frozen=True, slots=True)
class MovieIdentityResolution:
    movie_id_map: dict[int, int]
    metadata: dict


def resolve_dataset(version: str) -> DatasetPaths:
    if not version or not all(character.isalnum() or character in "-_" for character in version):
        raise ValueError("dataset version may contain only letters, numbers, '-' and '_'")
    if version == "fixed-v1":
        return DatasetPaths(
            cases=EVALUATION_ROOT / "data" / "fixed_cases.jsonl.gz",
            cohorts=EVALUATION_ROOT / "cohorts.json",
            manifest=EVALUATION_ROOT / "data" / "fixed_cases_manifest.json",
            movie_identities=EVALUATION_ROOT / "data" / "fixed_movie_identities.json.gz",
        )
    root = EVALUATION_ROOT / "data" / version
    return DatasetPaths(
        cases=root / "cases.jsonl.gz",
        cohorts=root / "cohorts.json",
        manifest=root / "manifest.json",
        movie_identities=root / "movie_identities.json.gz",
    )


def resolve_movie_identities(
    path: Path,
    snapshot_movie_ids: set[int],
) -> MovieIdentityResolution:
    identities = load_movie_identities(path)
    missing_identities = sorted(snapshot_movie_ids - identities.keys())
    if missing_identities:
        raise ValueError(
            f"movie identity snapshot is missing {len(missing_identities)} evaluation movie IDs: "
            f"{_preview(missing_identities)}"
        )

    selected_identities = {
        movie_id: identities[movie_id]
        for movie_id in snapshot_movie_ids
    }
    current_by_tmdb_id = load_current_movie_ids_by_tmdb(set(selected_identities.values()))
    missing_tmdb_ids = sorted(set(selected_identities.values()) - current_by_tmdb_id.keys())
    if missing_tmdb_ids:
        raise ValueError(
            f"evaluation catalog mismatch: {len(missing_tmdb_ids)} TMDB movies are missing "
            f"from DB: {_preview(missing_tmdb_ids)}"
        )

    movie_id_map = {
        snapshot_movie_id: current_by_tmdb_id[tmdb_id]
        for snapshot_movie_id, tmdb_id in selected_identities.items()
    }
    if len(set(movie_id_map.values())) != len(movie_id_map):
        raise ValueError("multiple evaluation movies resolve to the same runtime DB movie ID")
    remapped = [
        {
            "snapshot_movie_id": snapshot_movie_id,
            "tmdb_id": selected_identities[snapshot_movie_id],
            "runtime_movie_id": runtime_movie_id,
        }
        for snapshot_movie_id, runtime_movie_id in sorted(movie_id_map.items())
        if snapshot_movie_id != runtime_movie_id
    ]
    return MovieIdentityResolution(
        movie_id_map=movie_id_map,
        metadata={
            "strategy": "tmdb_id",
            "evaluation_movie_count": len(movie_id_map),
            "unchanged_movie_count": len(movie_id_map) - len(remapped),
            "remapped_movie_count": len(remapped),
            "remapped_movies": remapped,
        },
    )


def load_movie_identities(path: Path) -> dict[int, int]:
    if not path.is_file():
        raise FileNotFoundError(f"movie identity snapshot not found: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as source:
        payload = json.load(source)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("movies"), list):
        raise ValueError(f"{path}: unsupported movie identity snapshot")
    identities: dict[int, int] = {}
    tmdb_ids: set[int] = set()
    for values in payload["movies"]:
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError(f"{path}: each movie identity must be [movie_id, tmdb_id]")
        movie_id, tmdb_id = (int(values[0]), int(values[1]))
        if movie_id in identities or tmdb_id in tmdb_ids:
            raise ValueError(f"{path}: duplicate movie_id or tmdb_id")
        identities[movie_id] = tmdb_id
        tmdb_ids.add(tmdb_id)
    if not identities:
        raise ValueError(f"{path}: movie identity snapshot is empty")
    return identities


def write_movie_identities(path: Path, movie_ids: set[int]) -> None:
    if not movie_ids:
        raise ValueError("cannot write an empty movie identity snapshot")
    db = SessionLocal()
    try:
        rows = [
            row
            for chunk in _chunks(sorted(movie_ids))
            for row in db.execute(
                select(Movie.id, Movie.tmdb_id)
                .where(Movie.id.in_(chunk))
                .order_by(Movie.id)
            ).all()
        ]
    finally:
        db.close()
    identities = {
        int(movie_id): int(tmdb_id)
        for movie_id, tmdb_id in rows
        if tmdb_id is not None
    }
    missing = sorted(movie_ids - identities.keys())
    if missing:
        raise ValueError(
            f"cannot snapshot {len(missing)} movies that are missing or have no TMDB ID: "
            f"{_preview(missing)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schema_version": 1,
            "identity": "tmdb_id",
            "movies": [[movie_id, identities[movie_id]] for movie_id in sorted(identities)],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with path.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(payload)


def load_current_movie_ids_by_tmdb(tmdb_ids: set[int]) -> dict[int, int]:
    if not tmdb_ids:
        return {}
    db = SessionLocal()
    try:
        rows = [
            row
            for chunk in _chunks(sorted(tmdb_ids))
            for row in db.execute(
                select(Movie.tmdb_id, Movie.id).where(Movie.tmdb_id.in_(chunk))
            ).all()
        ]
    finally:
        db.close()
    return {
        int(tmdb_id): int(movie_id)
        for tmdb_id, movie_id in rows
        if tmdb_id is not None
    }


def _preview(values: list[int], limit: int = 20) -> str:
    selected = values[:limit]
    return f"{selected}{'...' if len(values) > limit else ''}"


def _chunks(values: list[int], size: int = 10_000) -> Iterator[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
