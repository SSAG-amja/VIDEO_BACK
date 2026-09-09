from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.db.session import engine
from tests.v3_long_term_eval.prepare import DEFAULT_USER_COUNTS, parse_user_counts


ROOT = Path("tests/v3_long_term_eval")


class ProgressBar:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.last_length = 0
        self.spinner_index = 0

    def update(self, percent: float, label: str) -> None:
        bounded = min(100.0, max(0.0, percent))
        width = 32
        filled = round(width * bounded / 100.0)
        spinner = "|/-\\"[self.spinner_index % 4]
        self.spinner_index += 1
        elapsed = format_duration(time.monotonic() - self.started)
        line = (
            f"\r[{('=' * filled).ljust(width, '.')}] "
            f"{bounded:6.2f}% {spinner} {label} ({elapsed})"
        )
        padding = " " * max(0, self.last_length - len(line))
        sys.stdout.write(line + padding)
        sys.stdout.flush()
        self.last_length = len(line)

    def finish(self, label: str) -> None:
        self.update(100.0, label)
        sys.stdout.write("\n")
        sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete V3 long-term NDCG evaluation pipeline"
    )
    parser.add_argument("--ontology-build-id", type=int, default=22)
    parser.add_argument(
        "--user-counts",
        type=parse_user_counts,
        default=DEFAULT_USER_COUNTS,
        help="Comma-separated nested cohort sizes",
    )
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--num-threads", type=int)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the current plan, seeded users, and newest shadow artifact",
    )
    parser.add_argument(
        "--cleanup-after",
        action="store_true",
        help="Delete evaluation users after result files are written",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_inputs()
    ROOT.joinpath("generated").mkdir(parents=True, exist_ok=True)
    ROOT.joinpath("outputs").mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "outputs" / "pipeline.log"
    progress = ProgressBar()
    log_lines: list[str] = []

    try:
        cohort_plans: dict[int, Path]
        anchor_plan: Path
        if args.resume:
            user_count = evaluation_user_count()
            if user_count <= 0:
                raise RuntimeError("no seeded evaluation users exist; run without --resume")
            cohort_plans = cohort_plan_paths(args.user_counts)
            anchor_plan = cohort_plans[min(args.user_counts)]
            progress.update(
                25.0,
                f"기존 평가 사용자 {user_count}명과 누적 계획 재사용",
            )
        else:
            progress.update(0.0, "MovieLens 매핑 및 집중 취향 사용자 표본 준비")
            prepare_records = run_module(
                "tests.v3_long_term_eval.prepare",
                ["--user-counts", ",".join(map(str, args.user_counts))],
                progress=progress,
                base_percent=0.0,
                end_percent=15.0,
                label="MovieLens 매핑 및 집중 취향 사용자 표본 준비",
                log_lines=log_lines,
            )
            if not any(record.get("format_version") == 1 for record in prepare_records):
                raise RuntimeError("preparation did not produce a valid summary")
            preparation = next(
                record for record in reversed(prepare_records)
                if record.get("format_version") == 1
            )
            cohort_plans = {
                int(user_count): Path(path)
                for user_count, path in preparation["outputs"]["cohort_plans"].items()
            }
            anchor_plan = cohort_plans[min(args.user_counts)]

            progress.update(15.0, "기존 평가 사용자 정리")
            deleted_count = cleanup_evaluation_users()
            progress.update(19.0, f"기존 평가 사용자 {deleted_count}명 정리")

            progress.update(19.0, "최대 누적 코호트 장기 행동 seed 삽입")
            apply_generated_seed(ROOT / "generated/01_seed_movielens_users.sql")
            progress.update(25.0, "최대 누적 코호트 장기 행동 seed 삽입 완료")

        comparison_rows = []
        anchor_user_count = min(args.user_counts)
        stage_span = 75.0 / len(args.user_counts)
        for stage_index, cohort_size in enumerate(args.user_counts):
            stage_start = 25.0 + stage_index * stage_span
            train_end = stage_start + stage_span * 0.8
            stage_end = stage_start + stage_span
            plan_path = cohort_plans[cohort_size]
            model_root = ROOT / "generated/models" / str(cohort_size)
            output_dir = ROOT / "outputs/scales" / str(cohort_size)

            if args.resume and model_root.is_dir():
                artifact_path = str(find_latest_shadow_artifact(model_root))
                progress.update(
                    train_end,
                    f"{cohort_size}명 기존 shadow 모델 재사용",
                )
            else:
                train_arguments = [
                    str(args.ontology_build_id),
                    "--data-cutoff-at",
                    str(plan_evaluation_as_of(plan_path)),
                    "--plan",
                    str(plan_path),
                    "--output-root",
                    str(model_root),
                ]
                if args.epochs is not None:
                    train_arguments.extend(("--epochs", str(args.epochs)))
                if args.num_threads is not None:
                    train_arguments.extend(("--num-threads", str(args.num_threads)))
                progress.update(stage_start, f"{cohort_size}명 shadow 모델 학습")
                train_records = run_module(
                    "tests.v3_long_term_eval.train_shadow_model",
                    train_arguments,
                    progress=progress,
                    base_percent=stage_start,
                    end_percent=train_end,
                    label=f"{cohort_size}명 shadow 모델 학습",
                    log_lines=log_lines,
                )
                train_result = next(
                    (
                        record
                        for record in reversed(train_records)
                        if record.get("status") == "ok"
                    ),
                    None,
                )
                if train_result is None or not train_result.get("artifact_path"):
                    raise RuntimeError(
                        f"{cohort_size}-user shadow training did not return an artifact path"
                    )
                artifact_path = str(train_result["artifact_path"])

            progress.update(
                train_end,
                f"{cohort_size}명 모델 고정 {anchor_user_count}명 순위 평가",
            )
            evaluate_records = run_module(
                "tests.v3_long_term_eval.evaluate",
                [
                    artifact_path,
                    "--plan",
                    str(anchor_plan),
                    "--output-dir",
                    str(output_dir),
                ],
                progress=progress,
                base_percent=train_end,
                end_percent=stage_end,
                label=(
                    f"{cohort_size}명 모델 고정 "
                    f"{anchor_user_count}명 순위 평가"
                ),
                log_lines=log_lines,
                user_progress=True,
            )
            evaluate_result = next(
                (
                    record
                    for record in reversed(evaluate_records)
                    if record.get("status") == "ok"
                ),
                None,
            )
            if evaluate_result is None:
                raise RuntimeError(f"{cohort_size}-user evaluation produced no result")
            comparison_rows.append(
                scale_comparison_row(
                    cohort_size,
                    summary_path=Path(evaluate_result["summary"]),
                    results_path=Path(evaluate_result["results"]),
                )
            )

        comparison_json, comparison_markdown = write_scale_comparison(
            comparison_rows,
            anchor_user_count=anchor_user_count,
        )

        if args.cleanup_after:
            cleanup_evaluation_users()
            cleanup_note = "평가 사용자 삭제 완료"
        else:
            cleanup_note = "평가 사용자는 결과 분석을 위해 DB에 유지"
        progress.finish("장기 취향 평가 완료")
        write_log(log_path, log_lines)
        print(f"비교 요약: {comparison_markdown}")
        print(f"비교 JSON: {comparison_json}")
        print(cleanup_note)
    except BaseException as exc:
        progress.update(100.0, "실패")
        sys.stdout.write("\n")
        log_lines.append(f"pipeline_error: {type(exc).__name__}: {exc}")
        write_log(log_path, log_lines)
        print(f"오류: {exc}", file=sys.stderr)
        print(f"로그: {log_path}", file=sys.stderr)
        raise SystemExit(1) from exc


def validate_inputs() -> None:
    missing = [
        path
        for path in (
            ROOT / "inputs/ratings.csv",
            ROOT / "inputs/links.csv",
            ROOT / "inputs/movies.csv",
        )
        if not path.is_file()
    ]
    if missing:
        raise SystemExit("MovieLens input missing: " + ", ".join(map(str, missing)))


def run_module(
    module: str,
    arguments: list[str],
    *,
    progress: ProgressBar,
    base_percent: float,
    end_percent: float,
    label: str,
    log_lines: list[str],
    user_progress: bool = False,
) -> list[dict]:
    command = [sys.executable, "-m", module, *arguments]
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=Path.cwd(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line.rstrip("\n"))
        output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    records: list[dict] = []
    output_finished = False
    while process.poll() is None or not output_finished:
        try:
            line = output_queue.get(timeout=0.2)
        except queue.Empty:
            progress.update(base_percent, label)
            continue
        if line is None:
            output_finished = True
            continue
        log_lines.append(f"[{module}] {line}")
        record = parse_json_line(line)
        if record is not None:
            records.append(record)
            if user_progress and record.get("status") == "user_complete":
                ordinal = int(record["ordinal"])
                total = max(1, int(record["total"]))
                completed = base_percent + (end_percent - base_percent) * ordinal / total
                progress.update(completed, f"사용자 순위 평가 {ordinal}/{total}")
                continue
        progress.update(base_percent, label)
    return_code = process.wait()
    reader.join(timeout=1.0)
    if return_code != 0:
        tail = "\n".join(log_lines[-15:])
        raise RuntimeError(f"{module} failed with exit code {return_code}\n{tail}")
    progress.update(end_percent, f"{label} 완료")
    return records


def cleanup_evaluation_users() -> int:
    with engine.begin() as connection:
        count = int(
            connection.scalar(
                text("SELECT count(*) FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test'")
            )
            or 0
        )
        connection.execute(
            text(
                """
                DELETE FROM recommendation_runs
                WHERE run_id IN (
                    SELECT DISTINCT recommendation.run_id
                    FROM ontology_recommendations AS recommendation
                    JOIN users AS app_user ON app_user.id = recommendation.user_id
                    WHERE app_user.email LIKE 'v3eval-ml-%@pinlm.test'
                )
                """
            )
        )
        connection.execute(
            text("DELETE FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test'")
        )
    return count


def evaluation_user_count() -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                text("SELECT count(*) FROM users WHERE email LIKE 'v3eval-ml-%@pinlm.test'")
            )
            or 0
        )


def find_latest_shadow_artifact(model_root: Path) -> Path:
    candidates = [
        path
        for path in model_root.iterdir()
        if path.is_dir() and path.joinpath("manifest.json").is_file()
    ] if model_root.is_dir() else []
    if not candidates:
        raise RuntimeError("no shadow artifact exists; run without --resume")
    return max(candidates, key=lambda path: path.joinpath("manifest.json").stat().st_mtime)


def cohort_plan_paths(user_counts: tuple[int, ...]) -> dict[int, Path]:
    result = {
        user_count: (
            ROOT / "generated/cohorts" / str(user_count) / "evaluation_plan.jsonl"
        )
        for user_count in user_counts
    }
    missing = [path for path in result.values() if not path.is_file()]
    if missing:
        raise RuntimeError("missing cohort plans: " + ", ".join(map(str, missing)))
    return result


def plan_evaluation_as_of(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                return str(json.loads(line)["evaluation_as_of"])
    raise RuntimeError(f"evaluation plan is empty: {path}")


def scale_comparison_row(
    training_user_count: int,
    *,
    summary_path: Path,
    results_path: Path,
) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result_rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    full_values = [
        float(row["ranking"]["ndcg"][str(row["candidate_count"])])
        for row in result_rows
    ]
    mean_ndcg = summary["overall"]["mean_ndcg"]
    return {
        "training_user_count": training_user_count,
        "evaluation_user_count": int(summary["user_count"]),
        "model_build_id": str(summary["model_build_id"]),
        "ndcg_at_10": float(mean_ndcg["10"]),
        "ndcg_at_20": float(mean_ndcg["20"]),
        "ndcg_at_30": float(mean_ndcg["30"]),
        "mean_full_holdout_ndcg": sum(full_values) / len(full_values),
        "summary_path": str(summary_path),
        "results_path": str(results_path),
    }


def write_scale_comparison(
    rows: list[dict],
    *,
    anchor_user_count: int,
) -> tuple[Path, Path]:
    output_root = ROOT / "outputs"
    output_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": "nested_training_population_with_fixed_anchor_evaluation",
        "anchor_evaluation_user_count": anchor_user_count,
        "rows": rows,
    }
    json_path = output_root / "scale_comparison.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = output_root / "scale_comparison.md"
    lines = [
        "# 사용자 규모별 장기 취향 NDCG",
        "",
        f"모든 모델은 동일한 기준 사용자 {anchor_user_count}명의 홀드아웃으로 평가한다.",
        "",
        "| 학습 사용자 | 평가 사용자 | NDCG@10 | NDCG@20 | NDCG@30 | 전체 홀드아웃 NDCG |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['training_user_count']} | {row['evaluation_user_count']} "
            f"| {row['ndcg_at_10']:.6f} | {row['ndcg_at_20']:.6f} "
            f"| {row['ndcg_at_30']:.6f} "
            f"| {row['mean_full_holdout_ndcg']:.6f} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def apply_generated_seed(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"generated seed SQL is missing: {path}")
    sql = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("\\")
    )
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()
        raw_connection.commit()
    except BaseException:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


def parse_json_line(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def write_log(path: Path, lines: list[str]) -> None:
    header = f"pipeline_finished_at={datetime.now().astimezone().isoformat()}\n"
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


if __name__ == "__main__":
    main()
