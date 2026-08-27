from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select, text

from app.crud.recsys.ontology import get_active_build
from app.db.session import SessionLocal
from app.models.mapping import MovieActor, MovieOtt, movie_directors, movie_genres, movie_keywords
from app.models.movie import Movie
from app.models.ontology import MovieOverviewSemanticSignal


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_runtime_provenance(movie_ids: set[int]) -> dict:
    if not movie_ids:
        raise ValueError("evaluation movie universe is empty")
    return {
        "git": _git_metadata(),
        "database": _database_metadata(movie_ids),
    }


def _git_metadata() -> dict:
    commit = _git("rev-parse", "HEAD") or _read_git_commit(Path(".git"))
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "commit": commit or None,
        "dirty": bool(status) if status is not None else None,
        "tracked_change_count": len(status.splitlines()) if status else 0,
        "source_tree_sha256": _source_tree_sha256(),
    }


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _read_git_commit(dot_git: Path) -> str | None:
    try:
        git_dir = dot_git
        if dot_git.is_file():
            pointer = dot_git.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return None
            git_dir = (dot_git.parent / pointer.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref_name = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref_name
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_file():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith(("#", "^")):
                    commit, name = line.split(" ", 1)
                    if name == ref_name:
                        return commit
    except (OSError, ValueError):
        return None
    return None


def _source_tree_sha256() -> str:
    digest = hashlib.sha256()
    roots = [Path("app"), Path("evaluation"), Path("assets/ontology")]
    suffixes = {".py", ".json", ".toml", ".txt"}
    files = [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes and "__pycache__" not in path.parts
    ]
    for path in sorted(files, key=lambda value: value.as_posix()):
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _database_metadata(movie_ids: set[int]) -> dict:
    ordered_ids = sorted(movie_ids)
    digest = hashlib.sha256()
    db = SessionLocal()
    try:
        existing_ids = set(
            db.scalars(select(Movie.id).where(Movie.id.in_(ordered_ids))).all()
        )
        missing_ids = sorted(movie_ids - existing_ids)
        if missing_ids:
            preview = missing_ids[:20]
            suffix = "..." if len(missing_ids) > len(preview) else ""
            raise ValueError(
                f"evaluation catalog mismatch: {len(missing_ids)} movie IDs are missing "
                f"from DB: {preview}{suffix}"
            )

        movie_columns = tuple(Movie.__table__.c)
        _hash_rows(
            digest,
            "movies",
            db.execute(select(*movie_columns).where(Movie.id.in_(ordered_ids)).order_by(Movie.id)),
        )
        relations = (
            ("movie_genres", movie_genres.c.movie_id, movie_genres.c.genre_id),
            ("movie_keywords", movie_keywords.c.movie_id, movie_keywords.c.keyword_id),
            ("movie_directors", movie_directors.c.movie_id, movie_directors.c.director_id),
            ("movie_actors", MovieActor.movie_id, MovieActor.actor_id),
            ("movie_otts", MovieOtt.movie_id, MovieOtt.ott_id),
        )
        for label, movie_column, feature_column in relations:
            _hash_rows(
                digest,
                label,
                db.execute(
                    select(movie_column, feature_column)
                    .where(movie_column.in_(ordered_ids))
                    .order_by(movie_column, feature_column)
                ),
            )

        signal = MovieOverviewSemanticSignal
        _hash_rows(
            digest,
            "movie_overview_semantic_signals",
            db.execute(
                select(
                    signal.movie_id,
                    signal.signal_type,
                    signal.signal_key,
                    signal.weight,
                    signal.confidence,
                    signal.overview_hash,
                    signal.asset_version,
                    signal.extractor_version,
                )
                .where(signal.movie_id.in_(ordered_ids))
                .order_by(signal.movie_id, signal.signal_type, signal.signal_key, signal.extractor_version)
            ),
        )

        alembic_versions = list(db.scalars(text("SELECT version_num FROM alembic_version ORDER BY version_num")))
        build = get_active_build(db)
        ontology = None
        if build is not None:
            ontology = {
                "id": int(build.id),
                "version": str(build.version),
                "source_hash": str(build.source_hash),
                "node_count": int(build.node_count),
                "edge_count": int(build.edge_count),
            }
        return {
            "alembic_versions": alembic_versions,
            "evaluation_movie_count": len(existing_ids),
            "catalog_sha256": digest.hexdigest(),
            "active_ontology_build": ontology,
        }
    finally:
        db.close()


def _hash_rows(digest, label: str, rows: Iterable) -> None:
    digest.update(label.encode("utf-8"))
    digest.update(b"\0")
    for row in rows:
        serialized = json.dumps(
            [_stable_value(value) for value in row],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest.update(serialized.encode("utf-8"))
        digest.update(b"\n")


def _stable_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (dict, list)):
        return value
    return str(value)
