from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from app.jobs.recsys.v3.features.feature_builder import export_item_features
from app.jobs.recsys.v3.features.feature_schemas import ItemFeatureExport


PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "z_v3_docs" / "diagnostics"


def build_export_diagnostics(
    export: ItemFeatureExport,
    *,
    elapsed_seconds: float,
    initial_rss_bytes: int | None,
    final_rss_bytes: int | None,
    peak_rss_bytes: int | None,
) -> dict[str, Any]:
    manifest = export.manifest
    matrix = export.item_features
    matrix_bytes = int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
    possible_coordinates = manifest.movie_count * manifest.feature_count
    family_diagnostics = []
    for item in manifest.family_diagnostics:
        coverage = item.coverage
        family_diagnostics.append(
            {
                "feature": item.feature.value,
                "relation_type": item.relation_type,
                "source_edge_count": item.source_edge_count,
                "retained_edge_count": item.retained_edge_count,
                "matrix_nnz": item.matrix_nnz,
                "total_movie_count": coverage.total_entity_count,
                "covered_movie_count": coverage.covered_entity_count,
                "movie_coverage_ratio": round(
                    coverage.covered_entity_count / coverage.total_entity_count,
                    8,
                ),
                "source_value_count": coverage.source_value_count,
                "retained_value_count": coverage.retained_value_count,
                "dropped_value_count": coverage.dropped_value_count,
                "drop_counts": [asdict(drop) for drop in coverage.drop_counts],
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "memory": {
            "initial_rss_bytes": initial_rss_bytes,
            "final_rss_bytes": final_rss_bytes,
            "peak_rss_bytes": peak_rss_bytes,
            "csr_bytes": matrix_bytes,
        },
        "matrix": {
            "shape": list(manifest.matrix_shape),
            "nnz": manifest.matrix_nnz,
            "density": manifest.matrix_nnz / possible_coordinates,
            "dtype": str(matrix.dtype),
        },
        "manifest": {
            "exporter_version": manifest.exporter_version,
            "ontology_build_id": manifest.ontology_build_id,
            "ontology_engine_name": manifest.ontology_engine_name,
            "ontology_schema_version": manifest.ontology_schema_version,
            "ontology_source_hash": manifest.ontology_source_hash,
            "movie_count": manifest.movie_count,
            "feature_count": manifest.feature_count,
            "movie_mapping_hash": manifest.movie_mapping_hash,
            "feature_mapping_hash": manifest.feature_mapping_hash,
            "export_hash": manifest.export_hash,
            "pruning_rules": manifest.pruning_rules,
            "family_diagnostics": family_diagnostics,
        },
    }


def _read_proc_memory_bytes(field: str) -> int | None:
    status_path = Path("/proc/self/status")
    if not status_path.exists():
        return None
    for line in status_path.read_text(encoding="ascii").splitlines():
        if not line.startswith(f"{field}:"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[2] != "kB":
            return None
        return int(parts[1]) * 1024
    return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def run(ontology_build_id: int, output_path: Path) -> dict[str, Any]:
    initial_rss_bytes = _read_proc_memory_bytes("VmRSS")
    started = time.monotonic()
    print(
        f"V3 item feature export started build_id={ontology_build_id} "
        f"output={output_path}",
        flush=True,
    )
    with SessionLocal() as db:
        export = export_item_features(db, ontology_build_id)
        db.rollback()
    elapsed_seconds = time.monotonic() - started
    diagnostics = build_export_diagnostics(
        export,
        elapsed_seconds=elapsed_seconds,
        initial_rss_bytes=initial_rss_bytes,
        final_rss_bytes=_read_proc_memory_bytes("VmRSS"),
        peak_rss_bytes=_read_proc_memory_bytes("VmHWM"),
    )
    _write_json_atomic(output_path, diagnostics)
    print(
        f"V3 item feature export completed build_id={ontology_build_id} "
        f"movies={export.manifest.movie_count} features={export.manifest.feature_count} "
        f"nnz={export.manifest.matrix_nnz} elapsed_seconds={elapsed_seconds:.3f}",
        flush=True,
    )
    return diagnostics


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export full-catalog V3 item feature diagnostics")
    parser.add_argument("ontology_build_id", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.ontology_build_id <= 0:
        raise ValueError("ontology build ID must be positive")
    output_path = args.output or (
        DEFAULT_OUTPUT_DIR / f"item_feature_export_build_{args.ontology_build_id}.json"
    )
    run(args.ontology_build_id, output_path)


if __name__ == "__main__":
    main(sys.argv[1:])
