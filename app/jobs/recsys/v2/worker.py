from app.db.session import SessionLocal
from app.jobs.recsys.v2.graph_build_pipeline import run_graph_build_pipeline


def run_worker() -> int:
    db = SessionLocal()
    try:
        return run_graph_build_pipeline(db)
    finally:
        db.close()


if __name__ == "__main__":
    build_id = run_worker()
    print(f"ontology v2 worker completed build_id={build_id}")
