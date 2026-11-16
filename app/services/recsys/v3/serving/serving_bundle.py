from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from app.services.recsys.v3.config import (
    ENGINE_NAME,
    ENGINE_VERSION,
    SERVING_BUNDLE_FORMAT_VERSION,
    SERVING_BUNDLE_ROOT,
)
from app.services.recsys.v3.errors import V3NotReadyError
from app.services.recsys.v3.domain.feature_registry import FEATURE_REGISTRY_VERSION
from app.services.recsys.v3.serving.model_store import RuntimeHybridArtifact, load_runtime_hybrid_artifact
from app.services.recsys.v3.policy.policy_config import policy_config_hash


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ServingBundle:
    bundle_id: str
    root: Path
    manifest: dict
    model: RuntimeHybridArtifact

    @property
    def ontology_build_id(self) -> int:
        return int(self.manifest["ontology_build_id"])

    @property
    def candidate_snapshot_id(self) -> str:
        return str(self.manifest["candidate_snapshot_id"])


class ServingBundleCache:
    def __init__(self, root: str | Path = SERVING_BUNDLE_ROOT):
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._pointer_hash: str | None = None
        self._failed_pointer_hash: str | None = None
        self._bundle: ServingBundle | None = None

    def get(self) -> ServingBundle:
        pointer_path = self.root / "active_bundle.json"
        try:
            pointer_bytes = pointer_path.read_bytes()
        except FileNotFoundError as exc:
            if self._bundle is not None:
                return self._bundle
            raise V3NotReadyError("V3 active serving bundle pointer does not exist") from exc
        pointer_hash = hashlib.sha256(pointer_bytes).hexdigest()
        with self._lock:
            if self._bundle is not None and pointer_hash == self._pointer_hash:
                return self._bundle
            if self._bundle is not None and pointer_hash == self._failed_pointer_hash:
                return self._bundle
            try:
                bundle = self._load(pointer_bytes)
            except Exception as exc:
                if self._bundle is not None:
                    self._failed_pointer_hash = pointer_hash
                    logger.exception("V3 bundle reload failed; keeping previous bundle")
                    return self._bundle
                raise V3NotReadyError(f"V3 active serving bundle is invalid: {exc}") from exc
            self._bundle = bundle
            self._pointer_hash = pointer_hash
            self._failed_pointer_hash = None
            return bundle

    def clear(self) -> None:
        with self._lock:
            self._pointer_hash = None
            self._failed_pointer_hash = None
            self._bundle = None

    def _load(self, pointer_bytes: bytes) -> ServingBundle:
        pointer = json.loads(pointer_bytes)
        if pointer.get("serving_bundle_format_version") != SERVING_BUNDLE_FORMAT_VERSION:
            raise ValueError("unsupported serving bundle pointer format")
        manifest_path = _contained_path(self.root, str(pointer.get("manifest_path", "")))
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != pointer.get("manifest_sha256"):
            raise ValueError("serving bundle manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        bundle_id = _validate_bundle_manifest(manifest)
        if bundle_id != pointer.get("bundle_id"):
            raise ValueError("serving bundle pointer ID mismatch")
        model_path = _contained_path(self.root, str(manifest["model_artifact_path"]))
        snapshot_path = _contained_path(self.root, str(manifest["candidate_snapshot_path"]))
        snapshot_manifest = json.loads((snapshot_path / "manifest.json").read_bytes())
        if _hash_file(snapshot_path / "manifest.json") != manifest[
            "candidate_snapshot_manifest_sha256"
        ]:
            raise ValueError("serving bundle candidate snapshot manifest changed")
        if snapshot_manifest.get("candidate_snapshot_id") != manifest["candidate_snapshot_id"]:
            raise ValueError("serving bundle candidate snapshot mismatch")
        if snapshot_manifest.get("model_build_id") != manifest["model_build_id"]:
            raise ValueError("serving bundle snapshot/model mismatch")
        model = load_runtime_hybrid_artifact(model_path)
        if model.model_build_id != manifest["model_build_id"]:
            raise ValueError("serving bundle model build mismatch")
        if model.ontology_build_id != manifest["ontology_build_id"]:
            raise ValueError("serving bundle model/ontology mismatch")
        model_manifest_path = model_path / "manifest.json"
        if _hash_file(model_manifest_path) != manifest["model_manifest_sha256"]:
            raise ValueError("serving bundle model manifest changed")
        return ServingBundle(
            bundle_id=bundle_id,
            root=self.root,
            manifest=manifest,
            model=model,
        )


def _validate_bundle_manifest(manifest: dict) -> str:
    if manifest.get("serving_bundle_format_version") != SERVING_BUNDLE_FORMAT_VERSION:
        raise ValueError("unsupported serving bundle format")
    if manifest.get("engine_name") != ENGINE_NAME or manifest.get("engine_version") != ENGINE_VERSION:
        raise ValueError("serving bundle engine version mismatch")
    if manifest.get("feature_registry_version") != FEATURE_REGISTRY_VERSION:
        raise ValueError("serving bundle feature registry mismatch")
    if manifest.get("policy_config_hash") != policy_config_hash():
        raise ValueError("serving bundle policy config mismatch")
    input_hash = str(manifest.get("input_hash", ""))
    expected_id = f"bundle-{input_hash[:24]}"
    if len(input_hash) != 64 or manifest.get("bundle_id") != expected_id:
        raise ValueError("serving bundle ID does not match its inputs")
    if not isinstance(manifest.get("ontology_build_id"), int):
        raise ValueError("serving bundle ontology build ID is invalid")
    if not str(manifest.get("candidate_snapshot_id", "")).startswith("cand-"):
        raise ValueError("serving bundle candidate snapshot ID is invalid")
    return expected_id


def _contained_path(root: Path, relative: str) -> Path:
    if not relative:
        raise ValueError("serving bundle path cannot be empty")
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError("serving bundle path escapes artifact root")
    return path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_DEFAULT_CACHE = ServingBundleCache()


def get_active_serving_bundle() -> ServingBundle:
    return _DEFAULT_CACHE.get()


def clear_serving_bundle_cache() -> None:
    _DEFAULT_CACHE.clear()
