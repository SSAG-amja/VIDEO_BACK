from __future__ import annotations

import json
import platform
import tempfile
from pathlib import Path


RANDOM_SEED = 42


def run_spike() -> dict[str, object]:
    import joblib
    import lightfm
    import numpy as np
    import scipy
    import sklearn
    from lightfm import LightFM
    from scipy.sparse import coo_matrix, csr_matrix, vstack

    interactions = coo_matrix(
        (
            np.ones(6, dtype=np.float32),
            (
                np.array([0, 0, 1, 1, 2, 2], dtype=np.int32),
                np.array([0, 1, 1, 2, 2, 3], dtype=np.int32),
            ),
        ),
        shape=(3, 4),
        dtype=np.float32,
    )

    # Identity columns preserve collaborative signal. Shared columns allow
    # feature-only inference for users and items absent from the interaction map.
    train_user_features = csr_matrix(
        np.array(
            [
                [1, 0, 0, 1, 0],
                [0, 1, 0, 1, 0],
                [0, 0, 1, 0, 1],
            ],
            dtype=np.float32,
        )
    )
    train_item_features = csr_matrix(
        np.array(
            [
                [1, 0, 0, 0, 1, 0],
                [0, 1, 0, 0, 1, 0],
                [0, 0, 1, 0, 0, 1],
                [0, 0, 0, 1, 0, 1],
            ],
            dtype=np.float32,
        )
    )

    model = LightFM(no_components=4, loss="warp", random_state=RANDOM_SEED)
    model.fit(
        interactions,
        user_features=train_user_features,
        item_features=train_item_features,
        epochs=10,
        num_threads=1,
        verbose=False,
    )

    extended_user_features = vstack(
        [
            train_user_features,
            csr_matrix(np.array([[0, 0, 0, 1, 0]], dtype=np.float32)),
        ],
        format="csr",
    )
    extended_item_features = vstack(
        [
            train_item_features,
            csr_matrix(np.array([[0, 0, 0, 0, 0, 1]], dtype=np.float32)),
        ],
        format="csr",
    )

    known_scores = model.predict(
        0,
        np.arange(4, dtype=np.int32),
        user_features=train_user_features,
        item_features=train_item_features,
        num_threads=1,
    )
    feature_only_scores = model.predict(
        3,
        np.arange(5, dtype=np.int32),
        user_features=extended_user_features,
        item_features=extended_item_features,
        num_threads=1,
    )

    if not np.isfinite(known_scores).all() or not np.isfinite(feature_only_scores).all():
        raise RuntimeError("LightFM produced non-finite scores")

    with tempfile.TemporaryDirectory(prefix="lightfm-v3-spike-") as temp_dir:
        artifact_path = Path(temp_dir) / "model.joblib"
        joblib.dump(model, artifact_path)
        reloaded_model = joblib.load(artifact_path)
        reloaded_scores = reloaded_model.predict(
            3,
            np.arange(5, dtype=np.int32),
            user_features=extended_user_features,
            item_features=extended_item_features,
            num_threads=1,
        )

    if not np.array_equal(feature_only_scores, reloaded_scores):
        raise RuntimeError("LightFM scores changed after artifact reload")

    return {
        "status": "ok",
        "python": platform.python_version(),
        "packages": {
            "lightfm": lightfm.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "random_seed": RANDOM_SEED,
        "known_prediction_count": int(known_scores.size),
        "feature_only_prediction_count": int(feature_only_scores.size),
        "feature_only_new_user": True,
        "feature_only_new_item": True,
        "artifact_reload_exact_match": True,
    }


def main() -> None:
    print(json.dumps(run_spike(), sort_keys=True))


if __name__ == "__main__":
    main()
