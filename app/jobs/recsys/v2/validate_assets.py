import json
from pathlib import Path
from typing import Any


ASSET_DIR = Path("assets/ontology")

DEFINITION_FILES = {
    "theme": "themes.json",
    "mood": "moods.json",
}

RELATION_FILES = [
    "keyword_theme_mood_rules.json",
    "genre_theme_mood_rules.json",
    "theme_relations.json",
    "mood_relations.json",
]

ALLOWED_DEFINITION_FIELDS = {"key", "label_ko", "label_en", "aliases", "description", "version"}
REQUIRED_DEFINITION_FIELDS = {"key", "label_ko", "label_en", "aliases", "description", "version"}

ALLOWED_RELATION_FIELDS = {
    "source_type",
    "source_key",
    "relation_type",
    "target_type",
    "target_key",
    "weight",
    "confidence",
    "source",
    "description",
    "version",
}
REQUIRED_RELATION_FIELDS = ALLOWED_RELATION_FIELDS

ALLOWED_NODE_TYPES = {"genre", "keyword", "theme", "mood"}
ALLOWED_RELATION_TYPES = {
    "implies_theme",
    "implies_mood",
    "related_to",
    "broader_than",
    "narrower_than",
    "evokes_mood",
    "compatible_with",
}
ALLOWED_SOURCES = {"manual_asset"}

RELATION_SIGNATURES = {
    ("keyword", "implies_theme", "theme"),
    ("keyword", "implies_mood", "mood"),
    ("genre", "implies_theme", "theme"),
    ("genre", "implies_mood", "mood"),
    ("theme", "related_to", "theme"),
    ("theme", "broader_than", "theme"),
    ("theme", "narrower_than", "theme"),
    ("theme", "evokes_mood", "mood"),
    ("mood", "compatible_with", "mood"),
}


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        errors.append(f"{path}: file is missing")
        return None
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: root must be an object")
        return None
    return data


def _validate_key(value: Any, *, path: Path, location: str, errors: list[str], allow_spaces: bool = False) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: {location} must be a non-empty string")
        return None
    normalized = value.strip()
    if normalized != value:
        errors.append(f"{path}: {location} must not contain leading/trailing whitespace")
    if not allow_spaces and " " in normalized:
        errors.append(f"{path}: {location} must use snake_case or DB label form without extra spaces: {value!r}")
    return normalized


def _validate_float_range(value: Any, *, path: Path, location: str, errors: list[str]) -> None:
    if not isinstance(value, (int, float)):
        errors.append(f"{path}: {location} must be a number")
        return
    if value < 0.0 or value > 1.0:
        errors.append(f"{path}: {location} must be between 0.0 and 1.0")


def _validate_definition_file(path: Path, node_type: str, errors: list[str]) -> set[str]:
    data = _load_json(path, errors)
    if data is None:
        return set()

    if not isinstance(data.get("version"), str) or not data["version"]:
        errors.append(f"{path}: version must be a non-empty string")

    items = data.get("items")
    if not isinstance(items, list):
        errors.append(f"{path}: items must be a list")
        return set()

    seen: set[str] = set()
    for index, item in enumerate(items):
        location = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: {location} must be an object")
            continue

        missing = REQUIRED_DEFINITION_FIELDS - set(item)
        extra = set(item) - ALLOWED_DEFINITION_FIELDS
        if missing:
            errors.append(f"{path}: {location} missing fields: {sorted(missing)}")
        if extra:
            errors.append(f"{path}: {location} unknown fields: {sorted(extra)}")

        key = _validate_key(item.get("key"), path=path, location=f"{location}.key", errors=errors)
        if key is None:
            continue
        if key in seen:
            errors.append(f"{path}: duplicate {node_type} key: {key}")
        seen.add(key)

        aliases = item.get("aliases")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            errors.append(f"{path}: {location}.aliases must be a list of strings")

        for field in ("label_ko", "label_en", "description", "version"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{path}: {location}.{field} must be a non-empty string")

    return seen


def _validate_relation_file(
    path: Path,
    *,
    theme_keys: set[str],
    mood_keys: set[str],
    errors: list[str],
) -> set[tuple[str, str, str, str, str]]:
    data = _load_json(path, errors)
    if data is None:
        return set()

    if not isinstance(data.get("version"), str) or not data["version"]:
        errors.append(f"{path}: version must be a non-empty string")

    relations = data.get("relations")
    if not isinstance(relations, list):
        errors.append(f"{path}: relations must be a list")
        return set()

    seen: set[tuple[str, str, str, str, str]] = set()
    for index, relation in enumerate(relations):
        location = f"relations[{index}]"
        if not isinstance(relation, dict):
            errors.append(f"{path}: {location} must be an object")
            continue

        missing = REQUIRED_RELATION_FIELDS - set(relation)
        extra = set(relation) - ALLOWED_RELATION_FIELDS
        if missing:
            errors.append(f"{path}: {location} missing fields: {sorted(missing)}")
        if extra:
            errors.append(f"{path}: {location} unknown fields: {sorted(extra)}")

        source_type = relation.get("source_type")
        target_type = relation.get("target_type")
        relation_type = relation.get("relation_type")
        source_key = _validate_key(
            relation.get("source_key"),
            path=path,
            location=f"{location}.source_key",
            errors=errors,
            allow_spaces=source_type in {"genre", "keyword"},
        )
        target_key = _validate_key(
            relation.get("target_key"),
            path=path,
            location=f"{location}.target_key",
            errors=errors,
            allow_spaces=target_type in {"genre", "keyword"},
        )

        if source_type not in ALLOWED_NODE_TYPES:
            errors.append(f"{path}: {location}.source_type not allowed: {source_type!r}")
        if target_type not in ALLOWED_NODE_TYPES:
            errors.append(f"{path}: {location}.target_type not allowed: {target_type!r}")
        if relation_type not in ALLOWED_RELATION_TYPES:
            errors.append(f"{path}: {location}.relation_type not allowed: {relation_type!r}")
        if (source_type, relation_type, target_type) not in RELATION_SIGNATURES:
            errors.append(
                f"{path}: {location} invalid relation signature: "
                f"{source_type!r} -{relation_type!r}-> {target_type!r}"
            )

        _validate_float_range(relation.get("weight"), path=path, location=f"{location}.weight", errors=errors)
        _validate_float_range(relation.get("confidence"), path=path, location=f"{location}.confidence", errors=errors)

        if relation.get("source") not in ALLOWED_SOURCES:
            errors.append(f"{path}: {location}.source not allowed: {relation.get('source')!r}")

        for field in ("description", "version"):
            if not isinstance(relation.get(field), str) or not relation[field].strip():
                errors.append(f"{path}: {location}.{field} must be a non-empty string")

        if source_type == "theme" and source_key not in theme_keys:
            errors.append(f"{path}: {location}.source_key references unknown theme: {source_key}")
        if target_type == "theme" and target_key not in theme_keys:
            errors.append(f"{path}: {location}.target_key references unknown theme: {target_key}")
        if source_type == "mood" and source_key not in mood_keys:
            errors.append(f"{path}: {location}.source_key references unknown mood: {source_key}")
        if target_type == "mood" and target_key not in mood_keys:
            errors.append(f"{path}: {location}.target_key references unknown mood: {target_key}")

        if source_type == target_type and source_key == target_key:
            errors.append(f"{path}: {location} self relation is not allowed: {source_type}:{source_key}")

        if source_key is None or target_key is None:
            continue
        signature = (str(source_type), source_key, str(relation_type), str(target_type), target_key)
        if signature in seen:
            errors.append(f"{path}: duplicate relation: {signature}")
        seen.add(signature)

    return seen


def validate_assets(asset_dir: Path | str = ASSET_DIR) -> list[str]:
    base_dir = Path(asset_dir)
    errors: list[str] = []

    theme_keys = _validate_definition_file(base_dir / DEFINITION_FILES["theme"], "theme", errors)
    mood_keys = _validate_definition_file(base_dir / DEFINITION_FILES["mood"], "mood", errors)

    global_relations: set[tuple[str, str, str, str, str]] = set()
    for filename in RELATION_FILES:
        relations = _validate_relation_file(base_dir / filename, theme_keys=theme_keys, mood_keys=mood_keys, errors=errors)
        duplicates = global_relations.intersection(relations)
        for duplicate in sorted(duplicates):
            errors.append(f"{filename}: duplicate relation across files: {duplicate}")
        global_relations.update(relations)

    return errors


if __name__ == "__main__":
    validation_errors = validate_assets()
    if validation_errors:
        raise SystemExit("\n".join(validation_errors))
    print("ontology assets validation passed")
