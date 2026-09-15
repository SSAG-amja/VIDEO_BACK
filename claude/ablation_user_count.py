"""유저 수(10/50/100/150/200/248)를 늘려가며 LightFM 성능이 실제로 개선되는지 확인하는 ablation.

목적: "협업 필터링은 유저가 많아야 잘된다"는 주장이 이 프로젝트 데이터에서 실제로 재현되는지
검증한다. DB에 유저를 매번 다시 시딩하지 않고, claude/data/train.csv·test.csv(ml-latest-small,
248명)에서 유저 수만 다르게 샘플링해 app/services/recsys/lightfm_model.py의 실제 프로덕션
학습/추론 함수를 그대로 사용한다. item_genre_tags(아이템 피처)만 실 DB에서 한 번 읽어온다
(읽기 전용, 쓰기 없음).

핵심 설계: 유저 목록은 고정된 순서로 미리 섞어두고 앞에서부터 N명을 뽑는 nested prefix
방식을 쓴다. 그래서 "고정된 앵커 10명"이 모든 조건(N=10,50,...,248)에 공통으로 포함되며,
이 앵커 10명의 NDCG@10이 다른 유저를 더 추가할수록 실제로 좋아지는지를 별도로 측정할 수
있다 (같은 유저를 계속 평가하므로 "유저 난이도 차이"가 아니라 "협업 신호 증가 효과"만
순수하게 분리해서 볼 수 있음).
"""

from __future__ import annotations

import json
import math
import random
import time

import pandas as pd
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.mapping import movie_genres
from app.services.recsys.lightfm_model import predict_scores_for_user, train_lightfm_model

DATA_DIR = "claude/data"
POSITIVE_STATUSES = {"PINNED", "SAVED"}
USER_COUNTS = [10, 50, 100, 150, 200, 248]
ANCHOR_SIZE = 10
NDCG_K = 10
RANDOM_SEED = 20260728


# 2026.07.28 김광원
# train.csv/test.csv와 ml movieId -> 실제 Movie.id 매핑을 읽어와 ablation에 필요한 형태로 정리한다.
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, dict[int, int]]:
    train_df = pd.read_csv(f"{DATA_DIR}/train.csv")
    test_df = pd.read_csv(f"{DATA_DIR}/test.csv")
    with open(f"{DATA_DIR}/seed_manifest.json") as f:
        manifest = json.load(f)
    movie_id_map = {int(ml_id): int(db_id) for ml_id, db_id in manifest["movie_id_map_ml_to_db"].items()}
    return train_df, test_df, movie_id_map


# 2026.07.28 김광원
# 고정된 아이템 풀(전체 248명 train에 등장하는, DB와 매칭되는 영화 전체)에 대해서만
# 장르 태그를 한 번 읽어온다 — 모든 조건이 같은 아이템 유니버스를 공유해야 유저 수만 변수로
# 남는다.
def load_item_genre_tags(item_ids: list[int]) -> dict[int, list[str]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(movie_genres.c.movie_id, movie_genres.c.genre_id).where(movie_genres.c.movie_id.in_(item_ids))
        ).all()
    tags: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
    for movie_id, genre_id in rows:
        tags[movie_id].append(f"genre:{genre_id}")
    return tags


def ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    if not relevant_ids:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 2) for i, movie_id in enumerate(ranked_ids[:k]) if movie_id in relevant_ids)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


# 2026.07.28 김광원
# 유저 수 N 하나에 대해 실제 프로덕션 함수(train_lightfm_model/predict_scores_for_user)로
# 학습·추론하고, 앵커 10명 + 전체 그룹의 NDCG@10과 학습/추론 소요 시간을 측정한다.
def run_condition(
    user_count: int,
    ordered_ml_user_ids: list[int],
    anchor_ml_user_ids: list[int],
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    movie_id_map: dict[int, int],
    item_ids: list[int],
    item_genre_tags: dict[int, list[str]],
) -> dict:
    sampled_ml_user_ids = ordered_ml_user_ids[:user_count]
    sampled_set = set(sampled_ml_user_ids)
    item_id_set = set(item_ids)

    condition_train = train_df[train_df["userId"].isin(sampled_set)]
    positive_train = condition_train[condition_train["status"].isin(POSITIVE_STATUSES)]

    interactions: list[tuple[int, int, float]] = []
    excluded_by_user: dict[int, set[int]] = {uid: set() for uid in sampled_ml_user_ids}
    for row in positive_train.itertuples():
        db_movie_id = movie_id_map.get(int(row.movieId))
        if db_movie_id is None or db_movie_id not in item_id_set:
            continue
        interactions.append((int(row.userId), db_movie_id, float(row.rating)))
        excluded_by_user[int(row.userId)].add(db_movie_id)

    train_started_at = time.perf_counter()
    model, dataset, item_feature_matrix = train_lightfm_model(
        sampled_ml_user_ids, item_ids, interactions, item_genre_tags
    )
    train_seconds = time.perf_counter() - train_started_at

    condition_test = test_df[test_df["userId"].isin(sampled_set)]
    positive_test = condition_test[condition_test["status"].isin(POSITIVE_STATUSES)]
    relevant_by_user: dict[int, set[int]] = {uid: set() for uid in sampled_ml_user_ids}
    for row in positive_test.itertuples():
        db_movie_id = movie_id_map.get(int(row.movieId))
        if db_movie_id is not None and db_movie_id in item_id_set:
            relevant_by_user[int(row.userId)].add(db_movie_id)

    predict_started_at = time.perf_counter()
    ndcg_by_user: dict[int, float] = {}
    for user_id in sampled_ml_user_ids:
        scores = predict_scores_for_user(model, dataset, item_feature_matrix, user_id, excluded_by_user[user_id])
        ranked_ids = [candidate.movie_id for candidate in scores]
        ndcg_by_user[user_id] = ndcg_at_k(ranked_ids, relevant_by_user[user_id], NDCG_K)
    predict_seconds = time.perf_counter() - predict_started_at

    anchor_ndcgs = [ndcg_by_user[uid] for uid in anchor_ml_user_ids if relevant_by_user.get(uid)]
    group_ndcgs = [score for uid, score in ndcg_by_user.items() if relevant_by_user.get(uid)]

    return {
        "user_count": user_count,
        "interaction_count": len(interactions),
        "train_seconds": round(train_seconds, 3),
        "predict_seconds_total": round(predict_seconds, 3),
        "anchor_ndcg10_mean": round(sum(anchor_ndcgs) / len(anchor_ndcgs), 4) if anchor_ndcgs else None,
        "anchor_users_with_ground_truth": len(anchor_ndcgs),
        "group_ndcg10_mean": round(sum(group_ndcgs) / len(group_ndcgs), 4) if group_ndcgs else None,
        "group_users_with_ground_truth": len(group_ndcgs),
    }


# 2026.07.28 김광원
# 6개 유저 수 조건을 순서대로 실행하고 결과를 표로 정리해 출력·저장한다.
def run() -> None:
    train_df, test_df, movie_id_map = load_data()

    all_ml_user_ids = sorted(train_df["userId"].unique().tolist())
    rng = random.Random(RANDOM_SEED)
    ordered_ml_user_ids = all_ml_user_ids[:]
    rng.shuffle(ordered_ml_user_ids)
    anchor_ml_user_ids = ordered_ml_user_ids[:ANCHOR_SIZE]

    mapped_train = train_df[train_df["movieId"].isin(movie_id_map)]
    item_ids = sorted({movie_id_map[int(m)] for m in mapped_train["movieId"].unique()})
    item_genre_tags = load_item_genre_tags(item_ids)

    results = []
    for user_count in USER_COUNTS:
        result = run_condition(
            user_count,
            ordered_ml_user_ids,
            anchor_ml_user_ids,
            train_df,
            test_df,
            movie_id_map,
            item_ids,
            item_genre_tags,
        )
        results.append(result)
        print(
            f"user_count={result['user_count']:>3} "
            f"interactions={result['interaction_count']:>6} "
            f"train_s={result['train_seconds']:>6} "
            f"predict_s={result['predict_seconds_total']:>6} "
            f"anchor_ndcg10={result['anchor_ndcg10_mean']} "
            f"group_ndcg10={result['group_ndcg10_mean']}"
        )

    with open("claude/results/ablation_user_count.json", "w") as f:
        json.dump(
            {
                "item_pool_size": len(item_ids),
                "anchor_ml_user_ids": anchor_ml_user_ids,
                "random_seed": RANDOM_SEED,
                "results": results,
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    run()
