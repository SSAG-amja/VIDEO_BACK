"""LightFM의 4가지 loss(warp/bpr/warp-kos/logistic) 중 warp를 쓰기로 한 선택이 실제로 이
프로젝트 데이터에서도 유리한지 직접 비교한다.

claude/data(ml-latest-small, 248명, 이미 이 세션에서 검증된 데이터)를 그대로 재사용하고,
동일한 유저/아이템/하이퍼파라미터(no_components=32, epochs=20, random_state=42)에서 loss만
바꿔가며 NDCG@10을 측정한다. logistic loss는 명시적 음성 레이블이 있어야 제대로 평가되므로
PASSED 상태를 음성(weight=-1)으로 명시적으로 같이 넣어준다 (안 그러면 불공정한 비교가 됨).
"""

from __future__ import annotations

import time

from lightfm import LightFM
from lightfm.data import Dataset

from claude.ablation_user_count import RANDOM_SEED, load_data, load_item_genre_tags, ndcg_at_k

POSITIVE_STATUSES = {"PINNED", "SAVED"}
NEGATIVE_STATUSES = {"PASSED"}
NO_COMPONENTS = 32
EPOCHS = 20
NDCG_K = 10
LOSSES = ["warp", "bpr", "warp-kos", "logistic"]


# 2026.07.28 김광원
# 248명 전체에 대해 학습/테스트 상호작용(양성 + logistic용 음성)을 한 번만 만들어 모든 loss가
# 동일한 데이터를 쓰도록 한다 — loss 종류만 변수로 남기기 위함.
def build_interactions(train_df, movie_id_map, item_id_set):
    positive_interactions = []
    negative_interactions = []
    excluded_by_user: dict[int, set[int]] = {}
    for row in train_df.itertuples():
        db_movie_id = movie_id_map.get(int(row.movieId))
        if db_movie_id is None or db_movie_id not in item_id_set:
            continue
        excluded_by_user.setdefault(int(row.userId), set()).add(db_movie_id)
        if row.status in POSITIVE_STATUSES:
            positive_interactions.append((int(row.userId), db_movie_id, float(row.rating)))
        elif row.status in NEGATIVE_STATUSES:
            negative_interactions.append((int(row.userId), db_movie_id, -1.0))
    return positive_interactions, negative_interactions, excluded_by_user


def build_ground_truth(test_df, movie_id_map, item_id_set):
    relevant_by_user: dict[int, set[int]] = {}
    for row in test_df.itertuples():
        if row.status not in POSITIVE_STATUSES:
            continue
        db_movie_id = movie_id_map.get(int(row.movieId))
        if db_movie_id is not None and db_movie_id in item_id_set:
            relevant_by_user.setdefault(int(row.userId), set()).add(db_movie_id)
    return relevant_by_user


# 2026.07.28 김광원
# 지정한 loss 하나로 학습·추론해 평균 NDCG@10과 학습 시간을 반환한다. logistic만 명시적 음성
# 상호작용을 함께 학습에 사용한다 (logistic loss의 정상적인 사용법).
def evaluate_loss(
    loss_name: str,
    user_ids: list[int],
    item_ids: list[int],
    positive_interactions,
    negative_interactions,
    item_genre_tags,
    excluded_by_user,
    relevant_by_user,
) -> dict:
    dataset = Dataset()
    dataset.fit(users=user_ids, items=item_ids, item_features=sorted({t for tags in item_genre_tags.values() for t in tags}))
    interactions_input = positive_interactions + (negative_interactions if loss_name == "logistic" else [])
    interaction_matrix, weight_matrix = dataset.build_interactions(interactions_input)
    item_feature_matrix = dataset.build_item_features(
        (item_id, item_genre_tags.get(item_id, [])) for item_id in item_ids
    )

    model = LightFM(loss=loss_name, no_components=NO_COMPONENTS, random_state=RANDOM_SEED)
    # k-OS loss는 sample_weight를 지원하지 않는다 (lightfm 라이브러리 제약).
    fit_sample_weight = None if loss_name == "warp-kos" else weight_matrix
    started_at = time.perf_counter()
    model.fit(interaction_matrix, item_features=item_feature_matrix, sample_weight=fit_sample_weight, epochs=EPOCHS)
    train_seconds = time.perf_counter() - started_at

    user_id_map, _, item_id_map, _ = dataset.mapping()
    inverse_item_id_map = {index: item_id for item_id, index in item_id_map.items()}
    item_indices = list(range(len(item_id_map)))

    ndcg_scores = []
    for uid in user_ids:
        if uid not in user_id_map or uid not in relevant_by_user:
            continue
        user_index = user_id_map[uid]
        scores = model.predict([user_index] * len(item_indices), item_indices, item_features=item_feature_matrix)
        ranked = sorted(
            ((inverse_item_id_map[i], scores[i]) for i in item_indices if inverse_item_id_map[i] not in excluded_by_user.get(uid, set())),
            key=lambda pair: pair[1],
            reverse=True,
        )
        ranked_ids = [movie_id for movie_id, _ in ranked]
        ndcg_scores.append(ndcg_at_k(ranked_ids, relevant_by_user[uid], NDCG_K))

    return {
        "loss": loss_name,
        "train_seconds": round(train_seconds, 3),
        "users_evaluated": len(ndcg_scores),
        "ndcg10_mean": round(sum(ndcg_scores) / len(ndcg_scores), 4) if ndcg_scores else None,
    }


def run() -> None:
    train_df, test_df, movie_id_map = load_data()
    user_ids = sorted(train_df["userId"].unique().tolist())
    mapped_train = train_df[train_df["movieId"].isin(movie_id_map)]
    item_ids = sorted({movie_id_map[int(m)] for m in mapped_train["movieId"].unique()})
    item_id_set = set(item_ids)
    item_genre_tags = load_item_genre_tags(item_ids)

    positive_interactions, negative_interactions, excluded_by_user = build_interactions(train_df, movie_id_map, item_id_set)
    relevant_by_user = build_ground_truth(test_df, movie_id_map, item_id_set)

    print(f"users={len(user_ids)} items={len(item_ids)} positive={len(positive_interactions)} negative={len(negative_interactions)}")
    for loss_name in LOSSES:
        result = evaluate_loss(
            loss_name, user_ids, item_ids, positive_interactions, negative_interactions,
            item_genre_tags, excluded_by_user, relevant_by_user,
        )
        print(result)


if __name__ == "__main__":
    run()
