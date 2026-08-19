import argparse
import importlib
import os
from pathlib import Path
from typing import Callable, cast

from evaluation.benchmark import run_benchmark
from evaluation.contracts import EvaluationEngine


DEFAULT_ENGINE_FACTORY = "app.services.recsys.evaluation:get_evaluation_engine"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all fixed recommendation benchmark cohorts.")
    parser.add_argument("test_name", help="Name used to group this benchmark run")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evaluation/data/fixed_cases.jsonl.gz"),
        help="Prepared fixed benchmark cases",
    )
    parser.add_argument(
        "--engine-factory",
        default=os.getenv("EVALUATION_ENGINE_FACTORY", DEFAULT_ENGINE_FACTORY),
        help="Import path in module:function form; defaults to the active app engine",
    )
    parser.add_argument("--output", type=Path, default=Path("evaluation_results"))
    args = parser.parse_args()
    factory = _load_factory(args.engine_factory)
    output_path = run_benchmark(
        test_name=args.test_name,
        cases_path=args.cases,
        output_root=args.output,
        engine_factory=factory,
    )
    print(output_path)


def _load_factory(import_path: str) -> Callable[[], EvaluationEngine]:
    try:
        module_name, function_name = import_path.split(":", 1)
    except ValueError as exc:
        raise ValueError("engine factory must use module:function format") from exc
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"{import_path} is not callable")
    return cast(Callable[[], EvaluationEngine], function)


if __name__ == "__main__":
    main()
