"""implicit(ALS) 협업 필터링 모델의 학습/추론 로직만 담당한다.

app/services/recsys/lightfm_model.py와 동일한 인터페이스 계약(train_*_model /
predict_scores_for_user)을 따른다 — 그래서 이 파일 하나만 있고 없고에 따라 파이프라인이
깨지지 않는다. 지금은 비교 검증 목적으로만 존재하며, app/jobs/recsys/lightfm_pipeline.py나
scheduler.py는 아무것도 참조하지 않는다 (필요 없어지면 이 파일만 지우면 됨).

implicit은 Surprise와 마찬가지로 아이템 피처(장르 등)를 지원하지 않으므로 item_genre_tags는
시그니처 호환을 위해서만 받고 사용하지 않는다. LightFM의 Dataset.fit()이 자동으로 해주던
user_id/movie_id -> 0-based 행렬 인덱스 매핑은 여기서 직접 관리한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from implicit.als import AlternatingLeastSquares
from scipy.sparse import csr_matrix

FACTORS = 32
ITERATIONS = 20
RANDOM_STATE = 42


@dataclass(frozen=True)
class ImplicitScore:
    movie_id: int
    score: float


@dataclass(frozen=True)
class ImplicitModelState:
    model: AlternatingLeastSquares
    user_id_to_index: dict[int, int]
    index_to_item_id: dict[int, int]
    user_items_matrix: csr_matrix


# 2026.07.28 김광원
# 유저-영화 긍정 상호작용으로 유저x아이템 sparse 행렬을 만들어 ALS를 학습한다. item_genre_tags는
# implicit이 아이템 피처를 지원하지 않아 인자로만 받고 사용하지 않는다. 반환값 2·3번째 자리는
# lightfm_model.py와의 시그니처 호환을 위해 None으로 채운다(dataset/item_feature_matrix 대응).
def train_implicit_model(
    user_ids: list[int],
    item_ids: list[int],
    interactions: list[tuple[int, int, float]],
    item_genre_tags: dict[int, list[str]],
) -> tuple[ImplicitModelState, None, None]:
    user_id_to_index = {user_id: index for index, user_id in enumerate(user_ids)}
    item_id_to_index = {item_id: index for index, item_id in enumerate(item_ids)}
    index_to_item_id = {index: item_id for item_id, index in item_id_to_index.items()}

    rows = [user_id_to_index[user_id] for user_id, movie_id, _ in interactions]
    cols = [item_id_to_index[movie_id] for _, movie_id, _ in interactions]
    weights = [weight for _, _, weight in interactions]
    user_items_matrix = csr_matrix((weights, (rows, cols)), shape=(len(user_ids), len(item_ids)))

    model = AlternatingLeastSquares(factors=FACTORS, iterations=ITERATIONS, random_state=RANDOM_STATE)
    model.fit(user_items_matrix)

    state = ImplicitModelState(model, user_id_to_index, index_to_item_id, user_items_matrix)
    return state, None, None


# 2026.07.28 김광원
# 학습된 ALS로 후보 아이템 전체에 대해 점수를 계산해 제외 목록을 뺀 뒤 점수 내림차순으로 반환한다.
# implicit은 LightFM처럼 유저 1명의 전체 아이템 점수를 벡터 연산 한 번에 계산해줘서 Surprise의
# 아이템별 반복 predict()보다 빠르다.
def predict_scores_for_user(
    state: ImplicitModelState,
    _dataset_placeholder,
    _item_feature_placeholder,
    user_id: int,
    excluded_movie_ids: set[int],
) -> list[ImplicitScore]:
    user_index = state.user_id_to_index.get(user_id)
    if user_index is None:
        return []

    item_indices, scores = state.model.recommend(
        user_index,
        state.user_items_matrix[user_index],
        N=len(state.index_to_item_id),
        filter_already_liked_items=False,
    )
    return sorted(
        (
            ImplicitScore(movie_id=state.index_to_item_id[int(index)], score=float(score))
            for index, score in zip(item_indices, scores)
            if state.index_to_item_id[int(index)] not in excluded_movie_ids
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
