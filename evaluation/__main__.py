import argparse
from functools import partial
from pathlib import Path

from evaluation.benchmark import run_benchmark, summary_path_for
from evaluation.datasets import resolve_dataset
from evaluation.engine import get_evaluation_engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all fixed recommendation benchmark cohorts.")
    parser.add_argument("test_name", help="Name used to group this benchmark run")
    parser.add_argument("--engine", help="Engine version; defaults to RECOMMENDATION_ENGINE")
    parser.add_argument("--dataset", default="fixed-v1", help="Fixed dataset version")
    parser.add_argument("--output", type=Path, default=Path("evaluation_results"))
    args = parser.parse_args()
    dataset = resolve_dataset(args.dataset)
    output_path = run_benchmark(
        test_name=args.test_name,
        cases_path=dataset.cases,
        output_root=args.output,
        engine_factory=partial(get_evaluation_engine, args.engine),
        cohorts_path=dataset.cohorts,
        manifest_path=dataset.manifest,
        movie_identities_path=dataset.movie_identities,
    )
    print(f"json: {output_path}")
    print(f"summary: {summary_path_for(output_path)}")
if __name__ == "__main__":
    main()
