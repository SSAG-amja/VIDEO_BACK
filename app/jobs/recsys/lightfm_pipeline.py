"""LightFM 기반 야간 배치. interaction_signals.py의 신호 추출/락 로직을 재사용하고,
학습된 LightFM 모델 점수로 추천 후보를 만들어 recommendations 테이블에 저장한다.

콜드스타트(상호작용 이력이 없는 신규 유저)는 LightFM이 다룰 수 없으므로 이 배치의 대상에서
빠지며, 그 유저들은 계속 app/services/recsys/dynamic_retriever.py의 온보딩 로직이 담당한다.
읽기 시점 서빙 코드(app/services/recsys/recommendation.py)는 recommendations 테이블을
그대로 읽기만 하므로 이 배치가 무엇으로 계산했든 수정할 필요가 없다.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging
from app.crud.recsys.movies import find_popular_movies
from app.crud.recsys.recommendations import replace_user_recommendation_rows
from app.crud.recsys.users import load_worker_user_ids
from app.db.session import SessionLocal
from app.jobs.recsys.interaction_signals import acquire_worker_lock, load_interaction_signals, release_worker_lock
from app.models.mapping import movie_genres
from app.models.recommendations import Recommendation
from app.services.recsys.lightfm_model import predict_scores_for_user, train_lightfm_model

logger = logging.getLogger(__name__)

SOURCE_LIGHTFM = "lightfm"
# 후보 검색(retrieval) 단계에서 인기도 상위 N개로 아이템 유니버스를 좁힌다.
# 실 카탈로그(117만 건 이상) 전체를 유저마다 스코어링하면 비효율적이라, 실제 서비스에서
# 흔히 쓰는 "후보 검색 후 랭킹" 2단계 구조를 따른다. 인기도 상위권 밖의 롱테일 영화는
# 상호작용이 있어도 이번 배치의 추천 후보에서 제외되는 트레이드오프가 있다.
ITEM_POOL_SIZE = 5000


# 2026.07.28 김광원
# advisory lock으로 중복 실행을 막고 LightFM 모델을 학습해 전체 유저 추천을 재계산한다.
def run_pipeline() -> None:
    with SessionLocal() as lock_db:
        if not acquire_worker_lock(lock_db):
            logger.warning("lightfm pipeline skipped reason=worker_already_running")
            return
        try:
            _run_pipeline_locked()
        finally:
            release_worker_lock(lock_db)


# 2026.07.28 김광원
# 유저별 긍정 상호작용을 모아 LightFM을 학습시키고, 유저마다 추천 후보를 저장한다.
def _run_pipeline_locked() -> None:
    with SessionLocal() as db:
        user_ids = load_worker_user_ids(db)
        item_ids = [movie.id for movie in find_popular_movies(db, excluded_movie_ids=set(), limit=ITEM_POOL_SIZE)]
        item_id_set = set(item_ids)
        item_genre_tags = _load_item_genre_tags(db, item_ids)

        positive_interactions: list[tuple[int, int, float]] = []
        excluded_movie_ids_by_user: dict[int, set[int]] = {}
        for user_id in user_ids:
            signals = load_interaction_signals(db, user_id)
            excluded_movie_ids_by_user[user_id] = {signal.movie_id for signal in signals if signal.exclude_from_feed}
            positive_interactions.extend(
                (user_id, signal.movie_id, signal.score)
                for signal in signals
                if signal.score > 0 and signal.movie_id in item_id_set
            )

    if not positive_interactions:
        logger.warning("lightfm pipeline skipped reason=no_positive_interactions")
        return

    model, dataset, item_feature_matrix = train_lightfm_model(user_ids, item_ids, positive_interactions, item_genre_tags)
    logger.info(
        "lightfm model trained users=%s items=%s interactions=%s",
        len(user_ids),
        len(item_ids),
        len(positive_interactions),
    )

    replaced_count = 0
    for user_id in user_ids:
        scores = predict_scores_for_user(
            model, dataset, item_feature_matrix, user_id, excluded_movie_ids_by_user.get(user_id, set())
        )
        if not scores:
            continue

        rows = [
            Recommendation(
                user_id=user_id,
                movie_id=candidate.movie_id,
                score=candidate.score,
                rank=rank,
                source=SOURCE_LIGHTFM,
                source_scores={SOURCE_LIGHTFM: candidate.score},
            )
            for rank, candidate in enumerate(scores[: settings.RECOMMENDATION_POOL_SIZE], start=1)
        ]
        with SessionLocal.begin() as write_db:
            replace_user_recommendation_rows(write_db, user_id, rows)
        replaced_count += 1

    logger.info("lightfm pipeline finished users=%s replaced=%s", len(user_ids), replaced_count)


# 2026.07.28 김광원
# 후보 아이템들의 장르를 movie_genres에서 조회해 LightFM item_features 태그로 변환한다.
def _load_item_genre_tags(db: Session, item_ids: list[int]) -> dict[int, list[str]]:
    rows = db.execute(
        select(movie_genres.c.movie_id, movie_genres.c.genre_id).where(movie_genres.c.movie_id.in_(item_ids))
    ).all()
    tags: dict[int, list[str]] = {item_id: [] for item_id in item_ids}
    for movie_id, genre_id in rows:
        tags[movie_id].append(f"genre:{genre_id}")
    return tags


if __name__ == "__main__":
    configure_logging()
    run_pipeline()
