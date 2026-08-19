from app.services.recsys.v3.errors import V3NotReadyError


def run_worker() -> None:
    raise V3NotReadyError("V3 training and materialization worker is not implemented yet")


if __name__ == "__main__":
    run_worker()
