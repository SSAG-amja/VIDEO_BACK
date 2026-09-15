"""Surprise(SVD) 추천 결과를 유저 수(10/50/100/150/200/500)별로 movieID 리스트 JSON으로 저장한다.

claude/pull_lightfm_lists.py의 로더와 claude/surprise_vs_lightfm_comparison.py의 유저ID
목록을 그대로 재사용해 LightFM 버전(claude/results_ml32m/lightfm_recommendations_*.json)과
동일한 방식으로 분할한다. app/services/recsys/surprise_model.py의 실제 학습/추론 함수를
그대로 호출하며, DB는 쓰지 않고 읽기만 한다.
"""

from __future__ import annotations

import json

from app.services.recsys.surprise_model import predict_scores_for_user, train_surprise_model
from claude.pull_lightfm_lists import (
    POSITIVE_STATUSES,
    build_positive_interactions,
    load_movie_id_map,
    load_ratings,
    rating_to_status,
    split_user_ratings,
)
from claude.surprise_vs_lightfm_comparison import (
    LINKS_PATH,
    RATINGS_PATH,
    USER_IDS_10,
    USER_IDS_50,
    USER_IDS_100,
    USER_IDS_150,
    USER_IDS_200,
)


# 2026.07.28 김광원
# 하나의 유저 수 조건에 대해 분할 -> Surprise 학습/추론 -> JSON 저장까지 실행한다.
def run_for_condition(
    label: str,
    target_user_ids: list[int] | None,
    movie_id_map: dict[int, int],
    all_rows: list[dict],
    output_path: str,
) -> None:
    target_set = set(target_user_ids) if target_user_ids is not None else None
    rows_by_user: dict[int, list[dict]] = {}
    for row in all_rows:
        if target_set is not None and row["user_id"] not in target_set:
            continue
        rows_by_user.setdefault(row["user_id"], []).append(row)

    train_rows_by_user: dict[int, list[dict]] = {}
    test_rows_by_user: dict[int, list[dict]] = {}
    for user_id, rows in rows_by_user.items():
        train_rows, test_rows = split_user_ratings(rows)
        train_rows_by_user[user_id] = train_rows
        test_rows_by_user[user_id] = test_rows

    interactions, excluded_by_user = build_positive_interactions(train_rows_by_user, movie_id_map)
    item_ids = sorted(
        {movie_id_map[m] for rows in rows_by_user.values() for r in rows if (m := r["movie_id"]) in movie_id_map}
    )

    user_ids_present = sorted(rows_by_user.keys())
    algo, trainset, item_id_set = train_surprise_model(user_ids_present, item_ids, interactions, {})

    output: dict[str, dict] = {}
    for user_id in user_ids_present:
        scores = predict_scores_for_user(algo, trainset, item_id_set, user_id, excluded_by_user.get(user_id, set()))
        recommendations = [candidate.movie_id for candidate in scores]
        test_relevant_movie_ids = sorted(
            {
                movie_id_map[r["movie_id"]]
                for r in test_rows_by_user.get(user_id, [])
                if r["movie_id"] in movie_id_map and rating_to_status(r["rating"]) in POSITIVE_STATUSES
            }
        )
        output[str(user_id)] = {
            "train_count": len(train_rows_by_user.get(user_id, [])),
            "test_count": len(test_rows_by_user.get(user_id, [])),
            "test_relevant_movie_ids": test_relevant_movie_ids,
            "recommendation_count": len(recommendations),
            "recommendations": recommendations,
        }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"[{label}] users={len(user_ids_present)} items={len(item_ids)} "
        f"interactions={len(interactions)} -> {output_path}"
    )


def run() -> None:
    movie_id_map = load_movie_id_map(LINKS_PATH)
    all_rows = load_ratings(RATINGS_PATH)

    conditions = [
        ("10", USER_IDS_10, "claude/results_ml32m/surprise_recommendations_10users.json"),
        ("50", USER_IDS_50, "claude/results_ml32m/surprise_recommendations_50users.json"),
        ("100", USER_IDS_100, "claude/results_ml32m/surprise_recommendations_100users.json"),
        ("150", USER_IDS_150, "claude/results_ml32m/surprise_recommendations_150users.json"),
        ("200", USER_IDS_200, "claude/results_ml32m/surprise_recommendations_200users.json"),
        ("500", None, "claude/results_ml32m/surprise_recommendations_500users.json"),
    ]
    for label, ids, output_path in conditions:
        run_for_condition(label, ids, movie_id_map, all_rows, output_path)


if __name__ == "__main__":
    run()
