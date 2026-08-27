from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.crud.recsys.recommendations import load_v3_candidate_publication_summary
from app.db.session import SessionLocal
from app.jobs.recsys.v3.training.artifact_publisher import load_hybrid_artifact
from app.jobs.recsys.v3.candidates.candidate_snapshot import load_candidate_snapshot
from app.models.ontology import OntologyBuild
from app.services.recsys.v3.config import (
    ENGINE_NAME,
    ENGINE_VERSION,
    SERVING_BUNDLE_FORMAT_VERSION,
    SERVING_BUNDLE_ROOT,
)
from app.services.recsys.v3.domain.feature_registry import FEATURE_REGISTRY_VERSION
from app.services.recsys.v3.domain.ontology_registry import ONTOLOGY_ENGINE_NAME, ONTOLOGY_SCHEMA_VERSION
from app.services.recsys.v3.policy.policy_config import policy_config_hash, policy_config_snapshot


def activate_serving_bundle(
    db: Session,
    *,
    model_artifact_path: str | Path,
    candidate_snapshot_path: str | Path,
    artifact_root: str | Path = SERVING_BUNDLE_ROOT,
    require_candidate_publication: bool = True,
) -> dict:
    root = Path(artifact_root).resolve()
    model_path = Path(model_artifact_path).resolve()
    snapshot_path = Path(candidate_snapshot_path).resolve()
    model = load_hybrid_artifact(model_path)
    snapshot = load_candidate_snapshot(snapshot_path)
    if snapshot.model_build_id != model.manifest["model_build_id"]:
        raise ValueError("candidate snapshot and model artifact build IDs differ")
    ontology_manifest = model.manifest["ontology"]
    ontology_build_id = int(ontology_manifest["build_id"])
    build = db.get(OntologyBuild, ontology_build_id)
    if build is None or build.status != "success":
        raise ValueError("serving bundle requires a successful ontology build")
    if build.engine_name != ONTOLOGY_ENGINE_NAME or build.schema_version != ONTOLOGY_SCHEMA_VERSION:
        raise ValueError("serving bundle ontology schema mismatch")
    if build.source_hash != ontology_manifest["source_hash"]:
        raise ValueError("serving bundle ontology source hash mismatch")

    if require_candidate_publication:
        candidate_count, user_count = load_v3_candidate_publication_summary(
            db,
            model_build_id=snapshot.model_build_id,
            candidate_snapshot_id=snapshot.snapshot_id,
        )
        if candidate_count != int(snapshot.manifest["candidate_count"]):
            raise ValueError("published candidate count does not match snapshot")
        if user_count != int(snapshot.manifest["successful_user_count"]):
            raise ValueError("published candidate user count does not match snapshot")

    policy_snapshot = policy_config_snapshot()
    inputs = {
        "serving_bundle_format_version": SERVING_BUNDLE_FORMAT_VERSION,
        "engine_name": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "model_build_id": str(model.manifest["model_build_id"]),
        "model_manifest_sha256": _hash_file(model_path / "manifest.json"),
        "ontology_build_id": ontology_build_id,
        "ontology_source_hash": build.source_hash,
        "candidate_snapshot_id": snapshot.snapshot_id,
        "candidate_snapshot_manifest_sha256": _hash_file(snapshot_path / "manifest.json"),
        "feature_registry_version": FEATURE_REGISTRY_VERSION,
        "policy_config_hash": policy_config_hash(policy_snapshot),
    }
    input_hash = _hash_json(inputs)
    bundle_id = f"bundle-{input_hash[:24]}"
    bundle_dir = root / "serving_bundles" / bundle_id
    manifest = {
        **inputs,
        "input_hash": input_hash,
        "bundle_id": bundle_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_artifact_path": _relative_to_root(root, model_path),
        "candidate_snapshot_path": _relative_to_root(root, snapshot_path),
        "policy_config": policy_snapshot,
    }
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_dir / "manifest.json"
    if manifest_path.exists():
        existing = _read_json(manifest_path)
        if {key: existing.get(key) for key in inputs} != inputs:
            raise ValueError("immutable serving bundle directory has conflicting inputs")
        manifest = existing
    else:
        _write_json_atomic(manifest_path, manifest)

    db.execute(
        update(OntologyBuild)
        .where(
            OntologyBuild.engine_name == ONTOLOGY_ENGINE_NAME,
            OntologyBuild.schema_version == ONTOLOGY_SCHEMA_VERSION,
            OntologyBuild.id != ontology_build_id,
            OntologyBuild.is_active.is_(True),
        )
        .values(is_active=False)
    )
    build.is_active = True
    db.flush()
    db.commit()

    pointer = {
        "serving_bundle_format_version": SERVING_BUNDLE_FORMAT_VERSION,
        "bundle_id": bundle_id,
        "manifest_path": _relative_to_root(root, manifest_path),
        "manifest_sha256": _hash_file(manifest_path),
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json_atomic(root / "active_bundle.json", pointer)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Activate a validated V3 serving bundle")
    parser.add_argument("model_artifact", type=Path)
    parser.add_argument("candidate_snapshot", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=Path(SERVING_BUNDLE_ROOT))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SessionLocal() as db:
        manifest = activate_serving_bundle(
            db,
            model_artifact_path=args.model_artifact,
            candidate_snapshot_path=args.candidate_snapshot,
            artifact_root=args.artifact_root,
        )
    print(json.dumps({"status": "ok", "bundle": manifest}, sort_keys=True))


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError as exc:
        raise ValueError(f"serving artifact must be inside artifact root: {path}") from exc


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _hash_json(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
