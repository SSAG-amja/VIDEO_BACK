"""LightFM(WARP)·Surprise(SVD)·implicit(ALS)를 ml-32m 500명 슈퍼셋에서 유저 수
(10/50/100/150/200/500)별로 직접 비교한다.

claude/ml32m_500user_raw(Downloads/ml-32m 3)와 claude/pull_lightfm_lists.py의 로더를 그대로
재사용해 유저별 timestamp 기준 70:30 분할, rating->status 매핑을 동일하게 적용한다. 지정된
정확한 유저ID 목록(중첩 부분집합, 10~500명)으로 6개 조건을 돌려 세 모델의 실제 프로덕션
학습/추론 함수(app/services/recsys/lightfm_model.py, surprise_model.py, implicit_model.py)를
그대로 호출해 NDCG@10과 학습/예측 소요 시간을 함께 측정한다.
"""

from __future__ import annotations

import json
import time

from app.services.recsys.implicit_model import predict_scores_for_user as implicit_predict
from app.services.recsys.implicit_model import train_implicit_model
from app.services.recsys.lightfm_model import predict_scores_for_user as lightfm_predict
from app.services.recsys.lightfm_model import train_lightfm_model
from app.services.recsys.surprise_model import predict_scores_for_user as surprise_predict
from app.services.recsys.surprise_model import train_surprise_model
from claude.ablation_user_count import load_item_genre_tags, ndcg_at_k
from claude.pull_lightfm_lists import (
    POSITIVE_STATUSES,
    build_positive_interactions,
    load_movie_id_map,
    load_ratings,
    rating_to_status,
    split_user_ratings,
)

RATINGS_PATH = "claude/ml32m_500user_raw/filtered_ratings.csv"
LINKS_PATH = "claude/ml32m_500user_raw/filtered_links.csv"
NDCG_K = 10

USER_IDS_10 = [35015, 47486, 52381, 151814, 157086, 160425, 161851, 191004, 196909, 198767]

USER_IDS_50 = [
    1476, 5691, 6762, 12646, 13170, 13984, 15348, 17135, 36564, 42347,
    43703, 48917, 50757, 52381, 62391, 72261, 72358, 75086, 79085, 79853,
    79972, 88452, 91810, 97665, 97801, 98445, 111200, 114233, 115351,
    118650, 121613, 133786, 135320, 139431, 146023, 152397, 152586,
    154273, 157086, 157282, 160425, 160906, 172551, 174187, 177174,
    181528, 184724, 191004, 197168, 199027,
]

USER_IDS_100 = [
    1476, 5684, 6759, 12646, 13159, 13783, 13967, 15348, 17098, 22467,
    30699, 31429, 32412, 34868, 34883, 35015, 36511, 37921, 38026, 38680,
    42347, 43703, 46444, 46645, 47486, 47945, 48917, 50757, 52381, 54450,
    55103, 62311, 63683, 66405, 70681, 72261, 73191, 74989, 74997, 78996,
    79660, 79853, 80713, 83442, 85664, 87414, 88328, 91668, 97518, 97801,
    98072, 98317, 99508, 109185, 111049, 114091, 115220, 115255, 116055, 116379,
    117625, 118475, 121613, 124282, 131300, 133228, 133602, 135159, 135926, 138558,
    139247, 141908, 145829, 145933, 148678, 151814, 152397, 154020, 157086, 157853,
    159789, 160425, 160693, 161851, 167579, 170326, 172551, 173983, 176974, 180664,
    180895, 181528, 183647, 184496, 191004, 192804, 194218, 195632, 196909, 198767,
]

USER_IDS_150 = [
    1476, 5684, 5691, 6762, 12646, 12667, 13159, 13170, 13967, 13984,
    15348, 15362, 17098, 17135, 22467, 22496, 30699, 30724, 31429,
    31463, 32466, 34883, 34909, 35015, 35049, 36564, 37921, 42347,
    42409, 43703, 43767, 46444, 46509, 46645, 46692, 47557, 47945,
    48007, 48917, 48977, 50757, 50825, 52381, 54450, 54530, 55179,
    62311, 62391, 63683, 66502, 70681, 72261, 72358, 73191, 73286,
    75086, 75119, 78996, 79085, 79660, 79767, 79853, 79972, 80841,
    83552, 85664, 85787, 87414, 87518, 88328, 88452, 91668, 91810,
    97518, 97665, 97801, 98072, 98317, 98445, 99508, 109185, 109304,
    111049, 111200, 114233, 115220, 115255, 115351, 116055, 116204,
    116379, 117625, 118650, 121613, 121783, 124282, 124456, 131300,
    133228, 133387, 133786, 135159, 135320, 135926, 138558, 138735,
    139431, 141908, 142078, 145829, 146023, 146123, 151814, 152397,
    152586, 154020, 154273, 157086, 157282, 157853, 158040, 160425,
    160693, 160906, 162088, 167773, 170326, 170517, 172551, 172770,
    173983, 174187, 176974, 177174, 180664, 181093, 181528, 181755,
    183647, 184496, 184724, 191004, 191267, 193078, 194218, 194454,
    195632, 197168, 198767, 199027,
]

USER_IDS_200 = [
    1476, 1477, 5684, 5691, 6759, 6762, 12646, 12667, 13159, 13170, 13783, 13797,
    13967, 13984, 15348, 15362, 17098, 17135, 22467, 22496, 30699, 30724, 31429,
    31463, 32412, 32466, 34868, 34883, 34900, 34909, 35015, 35049, 36511, 36564,
    37921, 37981, 38026, 38078, 38680, 38722, 42347, 42409, 43703, 43767, 46444,
    46509, 46645, 46692, 47486, 47557, 47945, 48007, 48917, 48977, 50757, 50825,
    52381, 52444, 54450, 54530, 55103, 55179, 62311, 62391, 63683, 63760, 66405,
    66502, 70681, 70771, 72261, 72358, 73191, 73286, 74989, 74997, 75086, 75119,
    78996, 79085, 79660, 79767, 79853, 79972, 80713, 80841, 83442, 83552, 85664,
    85787, 87414, 87518, 88328, 88452, 91668, 91810, 97518, 97665, 97801, 97955,
    98072, 98190, 98317, 98445, 99508, 99655, 109185, 109304, 111049, 111200,
    114091, 114233, 115220, 115255, 115351, 115405, 116055, 116204, 116379,
    116546, 117625, 117824, 118475, 118650, 121613, 121783, 124282, 124456,
    131300, 131498, 133228, 133387, 133602, 133786, 135159, 135320, 135926,
    136100, 138558, 138735, 139247, 139431, 141908, 142078, 145829, 145933,
    146023, 146123, 148678, 148869, 151814, 151992, 152397, 152586, 154020,
    154273, 157086, 157282, 157853, 158040, 159789, 159992, 160425, 160606,
    160693, 160906, 161851, 162088, 167579, 167773, 170326, 170517, 172551,
    172770, 173983, 174187, 176974, 177174, 180664, 180895, 180922, 181093,
    181528, 181755, 183647, 183898, 184496, 184724, 191004, 191267, 192804,
    193078, 194218, 194454, 195632, 195872, 196909, 197168, 198767, 199027,
]


# 2026.07.28 김광원
# 하나의 유저 수 조건에서 데이터 분할 -> 학습/추론(두 모델 모두) -> NDCG@10·소요시간 측정을 수행한다.
def evaluate_condition(label: str, target_user_ids: list[int] | None, movie_id_map, all_rows) -> dict:
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
    item_genre_tags = load_item_genre_tags(item_ids)

    relevant_by_user: dict[int, set[int]] = {}
    for user_id, rows in test_rows_by_user.items():
        relevant = {
            movie_id_map[r["movie_id"]]
            for r in rows
            if r["movie_id"] in movie_id_map and rating_to_status(r["rating"]) in POSITIVE_STATUSES
        }
        if relevant:
            relevant_by_user[user_id] = relevant

    user_ids_present = sorted(rows_by_user.keys())

    def evaluate_model(train_fn, predict_fn) -> dict:
        started_at = time.perf_counter()
        model_state = train_fn(user_ids_present, item_ids, interactions, item_genre_tags)
        train_seconds = time.perf_counter() - started_at

        started_at = time.perf_counter()
        ndcg_scores = []
        for user_id in user_ids_present:
            if user_id not in relevant_by_user:
                continue
            scores = predict_fn(*model_state, user_id, excluded_by_user.get(user_id, set()))
            ranked_ids = [candidate.movie_id for candidate in scores]
            ndcg_scores.append(ndcg_at_k(ranked_ids, relevant_by_user[user_id], NDCG_K))
        predict_seconds = time.perf_counter() - started_at

        return {
            "train_seconds": round(train_seconds, 3),
            "predict_seconds_total": round(predict_seconds, 3),
            "ndcg10_mean": round(sum(ndcg_scores) / len(ndcg_scores), 4) if ndcg_scores else None,
            "users_evaluated": len(ndcg_scores),
        }

    result = {
        "user_count": len(user_ids_present),
        "item_count": len(item_ids),
        "interaction_count": len(interactions),
        "lightfm": evaluate_model(train_lightfm_model, lightfm_predict),
        "surprise": evaluate_model(train_surprise_model, surprise_predict),
        "implicit": evaluate_model(train_implicit_model, implicit_predict),
    }
    print(
        f"[{label}] users={result['user_count']} items={result['item_count']} interactions={result['interaction_count']}\n"
        f"  lightfm : {result['lightfm']}\n"
        f"  surprise: {result['surprise']}\n"
        f"  implicit: {result['implicit']}"
    )
    return result


def run() -> None:
    movie_id_map = load_movie_id_map(LINKS_PATH)
    all_rows = load_ratings(RATINGS_PATH)

    conditions = [
        ("10", USER_IDS_10),
        ("50", USER_IDS_50),
        ("100", USER_IDS_100),
        ("150", USER_IDS_150),
        ("200", USER_IDS_200),
        ("500", None),
    ]
    all_results = {label: evaluate_condition(label, ids, movie_id_map, all_rows) for label, ids in conditions}

    with open("claude/results_ml32m/lightfm_vs_surprise_vs_implicit.json", "w") as f:
        json.dump(all_results, f, indent=2)


if __name__ == "__main__":
    run()
