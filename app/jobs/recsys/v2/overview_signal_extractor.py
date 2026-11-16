import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.jobs.recsys.v2.validate_assets import ASSET_DIR, validate_assets


EXTRACTOR_VERSION = "overview_signals_v1"
DEFAULT_BATCH_SIZE = 5_000
DEFAULT_INSERT_CHUNK_SIZE = 5_000
NOISY_ALIAS_TERMS = {
    "bond",
    "kind",
    "law",
    "past",
    "plot",
    "power",
    "sad",
    "success",
}


@dataclass(frozen=True)
class OverviewSignalRule:
    signal_type: str
    signal_key: str
    asset_version: str
    patterns: tuple[tuple[str, re.Pattern], ...]


def load_assets(asset_dir: Path | str = ASSET_DIR) -> dict[str, Any]:
    base_dir = Path(asset_dir)
    loaded: dict[str, Any] = {}
    for path in sorted(base_dir.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            loaded[path.name] = json.load(file)
    return loaded


def build_overview_rules(assets: dict[str, Any]) -> list[OverviewSignalRule]:
    rules: list[OverviewSignalRule] = []
    for signal_type, filename in (("theme", "themes.json"), ("mood", "moods.json")):
        payload = assets.get(filename, {})
        asset_version = payload.get("version") or "unknown"
        for item in payload.get("items", []):
            patterns = tuple(
                (term, compile_term_pattern(term))
                for term in overview_terms_for_item(item)
            )
            if not patterns:
                continue
            rules.append(
                OverviewSignalRule(
                    signal_type=signal_type,
                    signal_key=item["key"],
                    asset_version=asset_version,
                    patterns=patterns,
                )
            )
    return rules


def overview_terms_for_item(item: dict[str, Any]) -> list[str]:
    raw_terms = [
        (item.get("key"), False),
        (item.get("label_ko"), False),
        (item.get("label_en"), False),
        *((alias, True) for alias in item.get("aliases", [])),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for term, is_alias in raw_terms:
        if not isinstance(term, str):
            continue
        normalized = term.replace("_", " ").strip()
        if not is_usable_term(normalized, is_alias=is_alias):
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(normalized)
    return terms


def is_usable_term(term: str, *, is_alias: bool) -> bool:
    if not term:
        return False
    if is_alias and term.lower() in NOISY_ALIAS_TERMS:
        return False
    if any(ord(char) > 127 for char in term):
        return len(term) >= 2
    if is_alias and " " not in term and len(term) < 6:
        return False
    return len(term) >= 3


def compile_term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    if all(ord(char) < 128 for char in term):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def extract_movie_signals(movie_id: int, overview: str, rules: list[OverviewSignalRule]) -> list[dict[str, Any]]:
    overview_hash = hashlib.sha256(overview.encode("utf-8")).hexdigest()
    signals: list[dict[str, Any]] = []
    for rule in rules:
        matched_terms = [
            term
            for term, pattern in rule.patterns
            if pattern.search(overview)
        ]
        if not matched_terms:
            continue
        match_count = len(matched_terms)
        signals.append(
            {
                "movie_id": movie_id,
                "signal_type": rule.signal_type,
                "signal_key": rule.signal_key,
                "weight": min(0.75, 0.45 + (match_count * 0.08)),
                "confidence": min(0.70, 0.40 + (match_count * 0.08)),
                "matched_terms": json.dumps(matched_terms, ensure_ascii=True),
                "overview_hash": overview_hash,
                "asset_version": rule.asset_version,
                "extractor_version": EXTRACTOR_VERSION,
            }
        )
    return signals


def run_overview_signal_extraction(
    db: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    insert_chunk_size: int = DEFAULT_INSERT_CHUNK_SIZE,
    reset_existing: bool = True,
) -> int:
    validation_errors = validate_assets()
    if validation_errors:
        raise ValueError("ontology asset validation failed:\n" + "\n".join(validation_errors))

    rules = build_overview_rules(load_assets())
    if reset_existing:
        db.execute(
            text("DELETE FROM movie_overview_semantic_signals WHERE extractor_version = :extractor_version"),
            {"extractor_version": EXTRACTOR_VERSION},
        )
        db.commit()

    total_inserted = 0
    read_connection = engine.connect().execution_options(stream_results=True)
    try:
        result = read_connection.execute(
            text(
                """
                SELECT id, overview
                FROM movies
                WHERE overview IS NOT NULL AND length(trim(overview)) > 0
                ORDER BY id
                """
            )
        )

        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break

            pending: list[dict[str, Any]] = []
            for row in rows:
                pending.extend(extract_movie_signals(int(row.id), row.overview, rules))

            for start in range(0, len(pending), insert_chunk_size):
                chunk = pending[start : start + insert_chunk_size]
                if not chunk:
                    continue
                db.execute(
                    text(
                        """
                        INSERT INTO movie_overview_semantic_signals (
                            movie_id, signal_type, signal_key, weight, confidence,
                            matched_terms, overview_hash, asset_version, extractor_version,
                            created_at, updated_at
                        )
                        VALUES (
                            :movie_id, :signal_type, :signal_key, :weight, :confidence,
                            CAST(:matched_terms AS JSON), :overview_hash, :asset_version, :extractor_version,
                            now(), now()
                        )
                        ON CONFLICT (movie_id, signal_type, signal_key, extractor_version)
                        DO UPDATE SET
                            weight = EXCLUDED.weight,
                            confidence = EXCLUDED.confidence,
                            matched_terms = EXCLUDED.matched_terms,
                            overview_hash = EXCLUDED.overview_hash,
                            asset_version = EXCLUDED.asset_version,
                            updated_at = now()
                        """
                    ),
                    chunk,
                )
                total_inserted += len(chunk)
            db.commit()
            print(f"overview signals: processed through movie_id={rows[-1].id}, signals={total_inserted}", flush=True)
    finally:
        read_connection.close()

    return total_inserted


def run_worker() -> int:
    db = SessionLocal()
    try:
        return run_overview_signal_extraction(db)
    finally:
        db.close()


if __name__ == "__main__":
    signal_count = run_worker()
    print(f"overview signal extraction completed signals={signal_count}", flush=True)
