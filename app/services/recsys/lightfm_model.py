"""LightFM 협업 필터링 모델의 학습/추론 로직만 담당한다.

DB 세션을 직접 열거나 커밋하지 않는 순수 모듈이다. 데이터 조회와 recommendations 테이블
저장은 app/jobs/recsys/lightfm_pipeline.py가 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lightfm import LightFM
from lightfm.data import Dataset

NO_COMPONENTS = 32
EPOCHS = 20
LOSS = "warp"
RANDOM_STATE = 42


@dataclass(frozen=True)
class LightfmScore:
    movie_id: int
    score: float


# 2026.07.28 김광원
# 유저-영화 긍정 상호작용과 장르 피처로 LightFM 데이터셋을 구성하고 WARP 모델을 학습한다.
def train_lightfm_model(
    user_ids: list[int],
    item_ids: list[int],
    interactions: list[tuple[int, int, float]],
    item_genre_tags: dict[int, list[str]],
) -> tuple[LightFM, Dataset, object]:
    all_genre_tags = sorted({tag for tags in item_genre_tags.values() for tag in tags})

    dataset = Dataset()
    dataset.fit(users=user_ids, items=item_ids, item_features=all_genre_tags)
    interaction_matrix, weight_matrix = dataset.build_interactions(interactions)
    item_feature_matrix = dataset.build_item_features(
        (item_id, item_genre_tags.get(item_id, [])) for item_id in item_ids
    )

    model = LightFM(loss=LOSS, no_components=NO_COMPONENTS, random_state=RANDOM_STATE)
    model.fit(interaction_matrix, item_features=item_feature_matrix, sample_weight=weight_matrix, epochs=EPOCHS)
    return model, dataset, item_feature_matrix


# 2026.07.28 김광원
# 학습된 모델로 한 유저의 아이템별 점수를 계산해 제외 목록을 뺀 뒤 점수 내림차순으로 반환한다.
def predict_scores_for_user(
    model: LightFM,
    dataset: Dataset,
    item_feature_matrix,
    user_id: int,
    excluded_movie_ids: set[int],
) -> list[LightfmScore]:
    user_id_map, _, item_id_map, _ = dataset.mapping()
    if user_id not in user_id_map:
        return []

    user_index = user_id_map[user_id]
    item_indices = np.arange(len(item_id_map))
    scores = model.predict(np.repeat(user_index, len(item_indices)), item_indices, item_features=item_feature_matrix)

    inverse_item_id_map = {index: item_id for item_id, index in item_id_map.items()}
    return sorted(
        (
            LightfmScore(movie_id=inverse_item_id_map[i], score=float(scores[i]))
            for i in range(len(item_indices))
            if inverse_item_id_map[i] not in excluded_movie_ids
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
