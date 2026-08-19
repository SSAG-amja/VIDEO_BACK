"""Backward-compatible imports for the v1 rule-based recommendation worker."""

from app.jobs.recsys.v1 import worker as _worker

globals().update({name: value for name, value in vars(_worker).items() if not name.startswith("__")})


def run_pipeline() -> None:
    _worker.run_worker()


if __name__ == "__main__":
    _worker.configure_logging()
    _worker.run_worker()
