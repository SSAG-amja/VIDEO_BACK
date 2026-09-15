"""./ml-32m(10명)과 claude/ml32m_200user_raw(200명 중 지정 100명)에서 실제 LightFM 추천
리스트를 JSON으로 뽑는다.

유저별로 timestamp 기준(동일 timestamp는 원본 행 순서를 tie-break로 사용) 과거 70%를 학습,
최근 30%를 평가용으로 나눈다. 학습용 70%의 긍정 상호작용(PINNED/SAVED, 기존 rating_to_status
매핑과 동일)으로 app/services/recsys/lightfm_model.py의 실제 프로덕션 학습/추론 함수를 그대로
학습·추론시켜, 유저별 전체 후보 아이템에 대한 랭킹(top-K로 자르지 않은 전체 리스트)을 그대로
출력한다. DB는 장르 태그·영화 제목 조회에만 쓰고 쓰기는 하지 않는다.
"""

from __future__ import annotations

import csv
import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.mapping import movie_genres
from app.services.recsys.lightfm_model import predict_scores_for_user, train_lightfm_model

TRAIN_RATIO = 0.7
POSITIVE_STATUSES = {"PINNED", "SAVED"}

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

USER_IDS_50 = [
    1476, 5691, 6762, 12646, 13170, 13984, 15348, 17135, 36564, 42347,
    43703, 48917, 50757, 52381, 62391, 72261, 72358, 75086, 79085, 79853,
    79972, 88452, 91810, 97665, 97801, 98445, 111200, 114233, 115351,
    118650, 121613, 133786, 135320, 139431, 146023, 152397, 152586,
    154273, 157086, 157282, 160425, 160906, 172551, 174187, 177174,
    181528, 184724, 191004, 197168, 199027,
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


# 2026.07.28 김광원
# 기존 세션에서 확정한 rating -> status 매핑(0.5~1.5 PASSED, 2.0~3.0 NEUTRAL, 3.5~4.5 PINNED,
# 5.0 SAVED)을 그대로 재사용한다.
def rating_to_status(rating: float) -> str:
    if rating <= 1.5:
        return "PASSED"
    if rating <= 3.0:
        return "NEUTRAL"
    if rating <= 4.5:
        return "PINNED"
    return "SAVED"


# 2026.07.28 김광원
# filtered_links.csv의 videoBackMovieId 컬럼으로 movieId -> 실제 Movie.id를 직접 매핑한다.
def load_movie_id_map(links_path: str) -> dict[int, int]:
    mapping: dict[int, int] = {}
    with open(links_path) as f:
        for row in csv.DictReader(f):
            db_id = row.get("videoBackMovieId")
            if db_id:
                mapping[int(row["movieId"])] = int(db_id)
    return mapping


# 2026.07.28 김광원
# filtered_ratings.csv를 읽어 원본 행 순서(orig_row_index)를 tie-break용으로 함께 저장한다.
def load_ratings(ratings_path: str) -> list[dict]:
    rows = []
    with open(ratings_path) as f:
        for idx, row in enumerate(csv.DictReader(f)):
            rows.append(
                {
                    "user_id": int(row["userId"]),
                    "movie_id": int(row["movieId"]),
                    "rating": float(row["rating"]),
                    "timestamp": int(row["timestamp"]),
                    "orig_row_index": idx,
                }
            )
    return rows


# 2026.07.28 김광원
# (timestamp, 원본 행 순서) 기준 정렬 후 과거 70%/최근 30%로 나눈다.
def split_user_ratings(user_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    ordered = sorted(user_rows, key=lambda r: (r["timestamp"], r["orig_row_index"]))
    split_at = round(len(ordered) * TRAIN_RATIO)
    return ordered[:split_at], ordered[split_at:]


# 2026.07.28 김광원
# 학습용 70% 중 긍정 상태(PINNED/SAVED)만 LightFM 긍정 상호작용으로 변환하고, 학습에 쓰인
# 영화는 추천 후보에서 제외하도록 유저별 제외 목록도 함께 만든다.
def build_positive_interactions(
    train_rows_by_user: dict[int, list[dict]], movie_id_map: dict[int, int]
) -> tuple[list[tuple[int, int, float]], dict[int, set[int]]]:
    interactions: list[tuple[int, int, float]] = []
    excluded_by_user: dict[int, set[int]] = {}
    for user_id, rows in train_rows_by_user.items():
        excluded: set[int] = set()
        for row in rows:
            db_movie_id = movie_id_map.get(row["movie_id"])
            if db_movie_id is None:
                continue
            excluded.add(db_movie_id)
            if rating_to_status(row["rating"]) in POSITIVE_STATUSES:
                interactions.append((user_id, db_movie_id, row["rating"]))
        excluded_by_user[user_id] = excluded
    return interactions, excluded_by_user


def load_item_genre_tags(item_ids: list[int]) -> dict[int, list[str]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(movie_genres.c.movie_id, movie_genres.c.genre_id).where(movie_genres.c.movie_id.in_(item_ids))
        ).all()
    tags: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
    for movie_id, genre_id in rows:
        tags[movie_id].append(f"genre:{genre_id}")
    return tags


# 2026.07.28 김광원
# 하나의 데이터셋(10명 또는 100명)에 대해 분할 -> 학습 -> 추론 -> JSON 저장까지 전체를 실행한다.
def run_for_dataset(
    label: str,
    ratings_path: str,
    links_path: str,
    target_user_ids: list[int] | None,
    output_path: str,
) -> None:
    movie_id_map = load_movie_id_map(links_path)
    all_rows = load_ratings(ratings_path)
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

    user_ids_present = sorted(rows_by_user.keys())
    model, dataset, item_feature_matrix = train_lightfm_model(user_ids_present, item_ids, interactions, item_genre_tags)

    output: dict[str, dict] = {}
    for user_id in user_ids_present:
        scores = predict_scores_for_user(model, dataset, item_feature_matrix, user_id, excluded_by_user.get(user_id, set()))
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
        f"{label}: users={len(user_ids_present)} items={len(item_ids)} "
        f"interactions={len(interactions)} -> {output_path}"
    )


def run() -> None:
    run_for_dataset(
        "10-user (./ml-32m)",
        "ml-32m/filtered_ratings.csv",
        "ml-32m/filtered_links.csv",
        target_user_ids=None,
        output_path="claude/results_ml32m/lightfm_recommendations_10users.json",
    )
    run_for_dataset(
        "100-user (claude/ml32m_200user_raw, 지정 100명)",
        "claude/ml32m_200user_raw/filtered_ratings.csv",
        "claude/ml32m_200user_raw/filtered_links.csv",
        target_user_ids=USER_IDS_100,
        output_path="claude/results_ml32m/lightfm_recommendations_100users.json",
    )
    run_for_dataset(
        "200-user (claude/ml32m_200user_raw, 전체)",
        "claude/ml32m_200user_raw/filtered_ratings.csv",
        "claude/ml32m_200user_raw/filtered_links.csv",
        target_user_ids=None,
        output_path="claude/results_ml32m/lightfm_recommendations_200users.json",
    )
    run_for_dataset(
        "50-user (claude/ml32m_500user_raw, 지정 50명)",
        "claude/ml32m_500user_raw/filtered_ratings.csv",
        "claude/ml32m_500user_raw/filtered_links.csv",
        target_user_ids=USER_IDS_50,
        output_path="claude/results_ml32m/lightfm_recommendations_50users.json",
    )
    run_for_dataset(
        "150-user (claude/ml32m_500user_raw, 지정 150명)",
        "claude/ml32m_500user_raw/filtered_ratings.csv",
        "claude/ml32m_500user_raw/filtered_links.csv",
        target_user_ids=USER_IDS_150,
        output_path="claude/results_ml32m/lightfm_recommendations_150users.json",
    )
    run_for_dataset(
        "500-user (claude/ml32m_500user_raw, 전체)",
        "claude/ml32m_500user_raw/filtered_ratings.csv",
        "claude/ml32m_500user_raw/filtered_links.csv",
        target_user_ids=None,
        output_path="claude/results_ml32m/lightfm_recommendations_500users.json",
    )


if __name__ == "__main__":
    run()
