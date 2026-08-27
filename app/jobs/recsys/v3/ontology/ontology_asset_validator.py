from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.recsys.v3.domain.ontology_registry import get_relation_definition


ASSET_DIR = Path(__file__).resolve().parents[5] / "assets" / "ontology" / "v3"
REQUIRED_FILES = {
    "themes.json",
    "moods.json",
    "genre_theme_mood_rules.json",
    "keyword_theme_mood_rules.json",
    "theme_relations.json",
    "mood_relations.json",
}
RELATION_FILES = REQUIRED_FILES - {"themes.json", "moods.json"}
MAX_RELATION_COUNT = 120


@dataclass(frozen=True, slots=True)
class OntologyAssetValidationReport:
    asset_version: str
    theme_count: int
    mood_count: int
    relation_count: int
    derived_theme_count: int
    derived_mood_count: int


def load_assets(asset_dir: Path | str = ASSET_DIR) -> dict[str, Any]:
    base_dir = Path(asset_dir)
    assets: dict[str, Any] = {}
    for path in sorted(base_dir.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            assets[path.name] = json.load(file)
    return assets


def validate_assets(
    asset_dir: Path | str = ASSET_DIR,
) -> OntologyAssetValidationReport:
    assets = load_assets(asset_dir)
    errors: list[str] = []
    missing_files = sorted(REQUIRED_FILES - set(assets))
    unexpected_files = sorted(set(assets) - REQUIRED_FILES)
    if missing_files:
        errors.append(f"missing files={missing_files}")
    if unexpected_files:
        errors.append(f"unexpected files={unexpected_files}")
    if errors:
        raise ValueError("invalid V3 ontology assets: " + "; ".join(errors))

    file_versions = {str(payload.get("version", "")).strip() for payload in assets.values()}
    if len(file_versions) != 1 or not next(iter(file_versions), ""):
        errors.append(f"asset file versions must match versions={sorted(file_versions)}")
    asset_version = next(iter(file_versions), "")

    controlled_nodes: dict[str, set[str]] = {}
    for node_type, filename in (("theme", "themes.json"), ("mood", "moods.json")):
        keys: set[str] = set()
        for index, item in enumerate(assets[filename].get("items", [])):
            key = str(item.get("key", "")).strip()
            if not key:
                errors.append(f"{filename}:{index} has an empty key")
                continue
            if key in keys:
                errors.append(f"{filename}:{index} duplicates key={key}")
            keys.add(key)
            if not str(item.get("label_ko", "")).strip() or not str(
                item.get("label_en", "")
            ).strip():
                errors.append(f"{filename}:{index} key={key} requires Korean and English labels")
        controlled_nodes[node_type] = keys

    relation_keys: set[tuple[str, str, str, str, str]] = set()
    derived_themes: set[str] = set()
    derived_moods: set[str] = set()
    relation_count = 0
    for filename in sorted(RELATION_FILES):
        for index, relation in enumerate(assets[filename].get("relations", [])):
            relation_count += 1
            source_type = str(relation.get("source_type", ""))
            source_key = str(relation.get("source_key", "")).strip()
            relation_type = str(relation.get("relation_type", ""))
            target_type = str(relation.get("target_type", ""))
            target_key = str(relation.get("target_key", "")).strip()
            identity = (source_type, source_key, relation_type, target_type, target_key)
            if identity in relation_keys:
                errors.append(f"{filename}:{index} duplicates relation={identity}")
            relation_keys.add(identity)

            try:
                definition = get_relation_definition(
                    relation_type,
                    source_type=source_type,
                    target_type=target_type,
                )
                if not definition.active:
                    errors.append(f"{filename}:{index} uses inactive relation={relation_type}")
            except (KeyError, ValueError) as exc:
                errors.append(f"{filename}:{index} has invalid endpoint contract: {exc}")

            for endpoint_type, endpoint_key, endpoint_name in (
                (source_type, source_key, "source"),
                (target_type, target_key, "target"),
            ):
                if endpoint_type in controlled_nodes and endpoint_key not in controlled_nodes[endpoint_type]:
                    errors.append(
                        f"{filename}:{index} has unknown {endpoint_name} "
                        f"{endpoint_type}:{endpoint_key}"
                    )

            for value_name in ("weight", "confidence"):
                value = relation.get(value_name)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    errors.append(f"{filename}:{index} has invalid {value_name}={value}")
            if not str(relation.get("description", "")).strip():
                errors.append(f"{filename}:{index} requires a description")

            if relation_type == "suggests_theme":
                derived_themes.add(target_key)
            if relation_type in {"suggests_mood", "evokes_mood"}:
                derived_moods.add(target_key)

    missing_theme_paths = sorted(controlled_nodes["theme"] - derived_themes)
    missing_mood_paths = sorted(controlled_nodes["mood"] - derived_moods)
    if missing_theme_paths:
        errors.append(f"themes without derivation paths={missing_theme_paths}")
    if missing_mood_paths:
        errors.append(f"moods without derivation paths={missing_mood_paths}")
    if relation_count > MAX_RELATION_COUNT:
        errors.append(
            f"relation count exceeds enrichment cap count={relation_count} max={MAX_RELATION_COUNT}"
        )
    if errors:
        raise ValueError("invalid V3 ontology assets: " + "; ".join(errors))

    return OntologyAssetValidationReport(
        asset_version=asset_version,
        theme_count=len(controlled_nodes["theme"]),
        mood_count=len(controlled_nodes["mood"]),
        relation_count=relation_count,
        derived_theme_count=len(derived_themes),
        derived_mood_count=len(derived_moods),
    )


def validate_db_source_coverage(
    db: Session,
    *,
    asset_dir: Path | str = ASSET_DIR,
) -> dict[str, dict[str, int]]:
    assets = load_assets(asset_dir)
    source_keys: dict[str, set[str]] = {"genre": set(), "keyword": set()}
    for filename in RELATION_FILES:
        for relation in assets[filename].get("relations", []):
            source_type = relation.get("source_type")
            if source_type in source_keys:
                source_keys[source_type].add(str(relation["source_key"]).strip().lower())

    source_queries = {
        "genre": """
            SELECT lower(g.name) AS source_key, count(mg.movie_id) AS movie_count
            FROM genres g
            LEFT JOIN movie_genres mg ON mg.genre_id = g.id
            JOIN movies m ON m.id = mg.movie_id
            WHERE lower(g.name) = ANY(:source_keys)
              AND m.adult IS FALSE
              AND COALESCE(NULLIF(trim(m.title_ko), ''), NULLIF(trim(m.title), '')) IS NOT NULL
            GROUP BY lower(g.name)
        """,
        "keyword": """
            SELECT lower(k.name) AS source_key, count(mk.movie_id) AS movie_count
            FROM keywords k
            LEFT JOIN movie_keywords mk ON mk.keyword_id = k.id
            JOIN movies m ON m.id = mk.movie_id
            WHERE lower(k.name) = ANY(:source_keys)
              AND m.adult IS FALSE
              AND COALESCE(NULLIF(trim(m.title_ko), ''), NULLIF(trim(m.title), '')) IS NOT NULL
            GROUP BY lower(k.name)
        """,
    }
    coverage: dict[str, dict[str, int]] = {}
    missing_sources: list[str] = []
    for source_type, keys in source_keys.items():
        rows = db.execute(
            text(source_queries[source_type]),
            {"source_keys": sorted(keys)},
        ).all()
        counts = {str(row.source_key): int(row.movie_count) for row in rows}
        for key in sorted(keys):
            if counts.get(key, 0) == 0:
                missing_sources.append(f"{source_type}:{key}")
        coverage[source_type] = counts
    if missing_sources:
        raise ValueError(f"V3 ontology asset sources have no catalog mappings sources={missing_sources}")
    return coverage
