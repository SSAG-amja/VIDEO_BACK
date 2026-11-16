"""Backward-compatible imports for the v1 rule-based recommendation worker."""

from app.jobs.recsys.v1 import worker as _v1

globals().update({name: value for name, value in vars(_v1).items() if not name.startswith("__")})


def run_pipeline() -> None:
    _v1.run_worker()


if __name__ == "__main__":
    _v1.configure_logging()
    _v1.run_worker()
