"""Surprise(SVD) 협업 필터링 모델의 학습/추론 로직만 담당한다.

app/services/recsys/lightfm_model.py와 동일한 인터페이스 계약(train_*_model /
predict_scores_for_user)을 따른다 — 그래서 이 파일 하나만 있고 없고에 따라 파이프라인이
깨지지 않는다. 지금은 비교 검증 목적으로만 존재하며, app/jobs/recsys/lightfm_pipeline.py나
scheduler.py는 아무것도 참조하지 않는다 (필요 없어지면 이 파일만 지우면 됨).

Surprise는 아이템 피처(장르 등)를 지원하지 않으므로 item_genre_tags는 시그니처 호환을
위해서만 받고 실제로 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from surprise import Dataset, Reader, SVD
from surprise.trainset import Trainset

N_FACTORS = 32
N_EPOCHS = 20
RANDOM_STATE = 42


@dataclass(frozen=True)
class SurpriseScore:
    movie_id: int
    score: float


# 2026.07.28 김광원
# 유저-영화 긍정 상호작용으로 Surprise SVD를 학습한다. item_genre_tags는 Surprise가 아이템
# 피처를 지원하지 않아 인자로만 받고 사용하지 않는다.
def train_surprise_model(
    user_ids: list[int],
    item_ids: list[int],
    interactions: list[tuple[int, int, float]],
    item_genre_tags: dict[int, list[str]],
) -> tuple[SVD, Trainset, set[int]]:
    weights = [weight for _, _, weight in interactions]
    reader = Reader(rating_scale=(min(weights, default=0.0), max(weights, default=1.0)))
    ratings_df = pd.DataFrame(
        [(str(user_id), str(movie_id), weight) for user_id, movie_id, weight in interactions],
        columns=["user", "item", "rating"],
    )
    trainset = Dataset.load_from_df(ratings_df, reader).build_full_trainset()

    algo = SVD(n_factors=N_FACTORS, n_epochs=N_EPOCHS, random_state=RANDOM_STATE)
    algo.fit(trainset)
    return algo, trainset, set(item_ids)


# 2026.07.28 김광원
# 학습된 SVD로 후보 아이템 전체(제외 목록 제외)에 대해 하나씩 예측해 점수 내림차순으로
# 반환한다. Surprise는 LightFM과 달리 유저×전체아이템을 한 번에 계산하는 벡터 연산이 없어
# 아이템마다 predict()를 반복 호출한다 (성능은 실측으로 비교, claude/surprise_vs_lightfm_comparison.py 참고).
def predict_scores_for_user(
    algo: SVD,
    trainset: Trainset,
    item_ids: set[int],
    user_id: int,
    excluded_movie_ids: set[int],
) -> list[SurpriseScore]:
    candidates = item_ids - excluded_movie_ids
    predictions = [
        SurpriseScore(movie_id=movie_id, score=algo.predict(str(user_id), str(movie_id)).est)
        for movie_id in candidates
    ]
    return sorted(predictions, key=lambda candidate: candidate.score, reverse=True)
