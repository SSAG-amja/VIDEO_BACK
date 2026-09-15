# LightFM → 다른 협업 필터링 모델(Surprise/implicit 등) 교체 가이드 (2026.07.28)

## 이 문서의 목적

지금 프로덕션 추천 배치는 LightFM(WARP loss)을 쓴다. 이 문서는 **다른 AI 에이전트(코덱스 등)가
LightFM을 Surprise나 implicit 같은 다른 모델로 교체할 때 스스로 판단할 수 있도록** 현재 구조,
파일별 역할, 지켜야 할 인터페이스 계약, 각 대안 라이브러리의 주의사항, 검증 방법을 전부 기록한다.
문서 맨 아래에 그대로 복사해서 쓸 수 있는 프롬프트도 있다.

## 1. 현재 아키텍처 (데이터 흐름)

```
scheduler.py (매일 자동 실행)
  └─ lightfm_pipeline.run_pipeline()          # 오케스트레이션: DB 조회 → 학습 → 저장
       ├─ interaction_signals.py               # 유저 행동 신호 추출 + 중복실행 방지 락
       ├─ crud/recsys/movies.find_popular_movies()   # 아이템 후보 풀(인기도 상위 5,000개)
       ├─ crud/recsys/users.load_worker_user_ids()   # 학습 대상 유저(상호작용 이력 있는 유저만)
       ├─ services/recsys/lightfm_model.py       # ★ 모델 학습/추론 (교체 대상은 이 파일뿐)
       └─ crud/recsys/recommendations.replace_user_recommendation_rows()  # DB 저장
```

읽기 시점 서빙(`services/recsys/recommendation.py`, API 엔드포인트)은 `recommendations` 테이블을
그냥 읽기만 하므로, 배치가 어떤 모델로 채웠든 전혀 신경 쓰지 않는다. **모델을 바꿔도 이 파일들은
절대 건드릴 필요가 없다.**

## 2. 파일별 현재 상태와 역할

| 파일 | 상태 | 역할 | 모델 교체 시 |
|---|---|---|---|
| `app/services/recsys/lightfm_model.py` | LightFM 전용 | 학습(`train_lightfm_model`)/추론(`predict_scores_for_user`) 순수 로직. DB 세션 없음 | **이 파일만 통째로 갈아끼우면 됨** |
| `app/jobs/recsys/lightfm_pipeline.py` | 모델 비의존 오케스트레이션 | DB에서 유저/아이템/신호를 모아 위 두 함수를 호출하고 결과를 저장 | import 경로 1줄만 수정 (아래 3번 참고) |
| `app/jobs/recsys/interaction_signals.py` | 모델 비의존 | `load_interaction_signals`(pinned/watched/passed + 플레이리스트/게시글/좋아요/댓글 파생 신호), `acquire_worker_lock`/`release_worker_lock` | 안 건드림 |
| `app/jobs/recsys/scheduler.py` | 기존 파일, 1줄 수정됨 | 매일 정해진 시간에 파이프라인 실행 | import 대상만 바뀜 |
| `app/core/config.py` | 죽은 설정 2개 제거됨 | `RECOMMENDATION_POOL_SIZE`(500), `WORKER_SCHEDULE_HOUR/MINUTE/TIMEZONE` | 안 건드림 |
| `requirements.txt` | `lightfm` 추가됨 | 의존성 | `lightfm` → `scikit-surprise` 또는 `implicit`로 교체 |
| `Dockerfile` | `build-essential` 추가됨 | lightfm의 Cython 확장 빌드에 gcc 필요 | 아래 6번 참고 (라이브러리별로 필요 패키지 다름) |
| `app/jobs/recsys/rec_pipeline.py` | **삭제됨** | 예전 Rule-based 로직 | 참고할 필요 없음 |
| `app/jobs/recsys/evaluation/` | **삭제됨** | Rule-based↔LightFM 비교 하네스 | 참고할 필요 없음 |

## 3. 핵심 계약 (Interface Contract) — 반드시 지켜야 하는 부분

`lightfm_pipeline.py`는 아래 두 함수의 **시그니처와 반환 타입만** 알고 있고, 내부가 LightFM이든
Surprise든 implicit이든 전혀 신경 쓰지 않는다. **이 계약만 지키면 다른 파일은 하나도 안 바꿔도 된다.**

```python
# app/services/recsys/lightfm_model.py

@dataclass(frozen=True)
class LightfmScore:
    movie_id: int
    score: float

def train_lightfm_model(
    user_ids: list[int],
    item_ids: list[int],
    interactions: list[tuple[int, int, float]],   # (user_id, movie_id, weight) — 긍정 상호작용만
    item_genre_tags: dict[int, list[str]],        # {movie_id: ["genre:1", "genre:3", ...]}
) -> tuple[model, dataset, item_feature_matrix]:   # 이 3개 반환값은 predict 함수에 그대로 넘겨줄 "학습 결과 묶음"일 뿐, 타입 자체는 자유
    ...

def predict_scores_for_user(
    model, dataset, item_feature_matrix,
    user_id: int,
    excluded_movie_ids: set[int],                  # 학습에 이미 쓰인(=이미 본) 영화는 추천에서 제외
) -> list[LightfmScore]:                            # score 내림차순 정렬된 상태로 반환
    ...
```

`lightfm_pipeline.py`에서 실제로 쓰는 부분(수정 불필요, 그대로 유지됨):
```python
model, dataset, item_feature_matrix = train_lightfm_model(user_ids, item_ids, positive_interactions, item_genre_tags)
...
scores = predict_scores_for_user(model, dataset, item_feature_matrix, user_id, excluded_movie_ids_by_user.get(user_id, set()))
for rank, candidate in enumerate(scores[: settings.RECOMMENDATION_POOL_SIZE], start=1):
    Recommendation(user_id=user_id, movie_id=candidate.movie_id, score=candidate.score, rank=rank, source=SOURCE_LIGHTFM, ...)
```

**교체 시 유일하게 손댈 곳**: `lightfm_pipeline.py` 4번째 import 줄
```python
from app.services.recsys.lightfm_model import predict_scores_for_user, train_lightfm_model
```
→ 새 모듈 이름으로만 바꾸면 됨 (예: `from app.services.recsys.surprise_model import ...`). `SOURCE_LIGHTFM = "lightfm"` 상수도 `"surprise"`/`"implicit"` 등으로 바꿔주면 `recommendations.source` 컬럼에 어떤 모델이 채웠는지 구분 가능.

## 4. 데이터 소스 (교체해도 그대로 재사용 가능)

- `interaction_signals.load_interaction_signals(db, user_id) -> list[InteractionSignal(movie_id, score, exclude_from_feed)]`
  — pinned(4.0)/watched(6.0)/passed(-3.0) + 플레이리스트 추가(6.0)/게시글(7.0)/좋아요(1.0)/댓글(1.5) 파생 신호, 최근성 감쇠 적용됨. `score > 0`인 것만 긍정 상호작용으로 씀.
- `crud.recsys.movies.find_popular_movies(db, excluded_movie_ids, limit) -> list[Movie]`
  — 아이템 후보 풀(현재 `ITEM_POOL_SIZE=5000`, 인기도 상위). 카탈로그 전체(117만 건)를 다 쓰지 않는 이유는 `lightfm_pipeline.py` 상단 주석 참고.
- `crud.recsys.users.load_worker_user_ids(db) -> list[int]`
  — 학습 대상 유저(콜드스타트 유저는 여기서 자동 제외됨 — 어떤 모델을 쓰든 콜드스타트는 별도로 `dynamic_retriever.py`가 담당).
- 아이템 피처(장르): `app/models/mapping.py`의 `movie_genres` 테이블(`movie_id`, `genre_id`)에서 직접 조회. `lightfm_pipeline.py`의 `_load_item_genre_tags()` 참고.
- 저장: `crud.recsys.recommendations.replace_user_recommendation_rows(db, user_id, rows: list[Recommendation])` — 유저의 기존 추천을 통째로 지우고 새로 씀.
- `Recommendation` 모델 컬럼(`app/models/recommendations.py`): `user_id, movie_id, score, rank, source, source_scores(JSON)`.

## 5. 현재 하이퍼파라미터 — 어디까지 실측 검증했는지 (정직하게)

`app/services/recsys/lightfm_model.py`의 `NO_COMPONENTS=32, EPOCHS=20, LOSS="warp", RANDOM_STATE=42` 중:

- **`loss="warp"`만 실측으로 검증됨.** `claude/loss_function_comparison.py`로 warp/bpr/warp-kos/logistic 4개를 같은 데이터(claude/data, 248명)로 비교한 결과 NDCG@10 기준 warp(0.2031)가 1위, bpr(0.1119)의 약 2배, logistic(0.0120)은 사실상 사용 불가 — 결과는 `claude/loss_function_comparison.py` 실행 로그 참고.
- **`no_components=32`, `epochs=20`은 튜닝한 적 없음.** LightFM 공식 예제의 관례적인 값을 그대로 쓴 것이고, 그리드서치 등으로 검증하지 않았다. 새 모델로 교체할 때 이 숫자들을 "검증된 최적값"으로 오해하면 안 된다.

## 6. 대안 라이브러리별 주의사항

### 6-1. Surprise (`scikit-surprise`)
- 순수 협업 필터링(SVD, SVD++, KNNBaseline 등)만 지원. **아이템 피처(장르 등) 자체를 지원하지 않는다** — `item_genre_tags` 관련 코드(4번의 `movie_genres` 조회)를 통째로 안 써도 됨. 콘텐츠 정보를 살리고 싶으면 별도 로직을 더 만들어야 함(이 프로젝트가 LightFM을 쓰는 핵심 이유이기도 함 — `claude/docs/lightfm_integration.md` 참고).
- 원래 "명시적 별점 예측"용으로 설계됨. implicit feedback(우리 프로젝트의 `interaction_signals` 점수)을 그대로 넣으려면 `Reader(rating_scale=(min, max))`를 점수 범위에 맞게 지정해야 함.
- **top-N 추천 리스트를 직접 안 만들어준다.** `algo.predict(user_id, item_id)`로 (user, item) 쌍 하나씩 예측만 해주므로, `predict_scores_for_user`에서 후보 아이템 전체에 대해 반복 호출 후 직접 정렬해야 함 (지금 `lightfm_model.py`의 `predict_scores_for_user`와 같은 패턴으로 감싸면 됨).
- 설치 시 gcc 필요 (Cython 확장) — `Dockerfile`의 `build-essential`은 그대로 둬도 됨.

### 6-2. implicit (`benfred/implicit`)
- ALS/BPR/LMF 등 구현, Cython/GPU 최적화로 빠름. **역시 아이템 피처를 기본 지원하지 않는다** (최신 버전에 일부 하이브리드 기능이 생겼지만 LightFM만큼 성숙하지 않음 — 실제로 쓸 거면 사전 조사 필요).
- 입력이 "유저×아이템 sparse matrix"라서, LightFM의 `Dataset.fit()`이 자동으로 해주던 **user_id/movie_id → 0-based 행렬 인덱스 매핑을 직접 짜야 한다.** (`{user_id: index}`, `{index: user_id}` 딕셔너리를 직접 관리)
- `pip install implicit`는 OpenBLAS 등 추가 시스템 패키지가 필요할 수 있음 — Dockerfile에 `libopenblas-dev` 등이 더 필요한지 실제 빌드로 확인 필수 (지금 `build-essential`만으론 부족할 수 있음).

### 6-3. 공통 체크리스트 (라이브러리 상관없이)
- `requirements.txt`에서 `lightfm` 줄 교체
- 새 라이브러리가 실제로 Dockerfile 빌드 중 컴파일되는지 확인 (아래 7번 검증 방법의 "실제 이미지 빌드" 단계 필수 — 이전에 lightfm도 처음엔 `build-essential` 누락으로 빌드 실패했었음)
- `LightfmScore` 데이터클래스 이름을 그대로 쓰든 새 이름으로 바꾸든 상관없음 — 어차피 `lightfm_pipeline.py`는 `.movie_id`, `.score` 속성만 읽음

## 7. 검증 방법 (교체 후 반드시 실행)

```bash
# 1) 실제 배포 Dockerfile로 이미지가 빌드되는지 (새 라이브러리 컴파일 확인)
docker build -t pinlm-back-verify -f Dockerfile .

# 2) 전체 앱 import 무결성 (깨진 참조 없는지)
docker run --rm -v "$(pwd)":/back -w /back pinlm-back-verify python -c \
  "import app.main; import app.jobs.recsys.scheduler; print('OK')"

# 3) 배치 실제 실행 (DB/Redis 켜져 있어야 함: docker compose up -d db redis)
docker run --rm --network video_back_default --env-file .env \
  -e DB_HOST=db -e DB_PORT=5432 -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -v "$(pwd)":/back -w /back pinlm-back-verify python -m app.jobs.recsys.lightfm_pipeline

# 4) DB에 반영됐는지
# SELECT source, count(*), count(DISTINCT user_id) FROM recommendations GROUP BY source;
```

## 8. 재사용 가능한 데이터/스크립트 자산 (claude/, 전부 gitignore됨)

- `claude/data/` — ml-latest-small 248명 train/test (timestamp 기준 70:30 분할 완료, `movie_id_map_ml_to_db` 매핑 포함)
- `claude/ml32m_200user_raw/`, `claude/ml32m_500user_raw/` — MovieLens 32M에서 뽑은 200명/500명 서브셋 원본
- `claude/ablation_user_count.py` — 유저 수 늘려가며 NDCG@10 추세 측정 (10~248명, 실측 완료)
- `claude/loss_function_comparison.py` — LightFM 4개 loss 비교 (실측 완료, 위 5번 참고)
- `claude/pull_lightfm_lists.py` — 유저별 전체 추천 movieID 리스트 뽑기

이 스크립트들은 전부 `app/services/recsys/lightfm_model.py`의 `train_lightfm_model`/`predict_scores_for_user`를 직접 import해서 쓰므로, **모델을 교체한 후 같은 스크립트로 새 모델 성능(NDCG@10)을 곧바로 재측정**할 수 있다 — 새로 만들 필요 없음.

## 9. 코덱스에게 전달할 프롬프트

아래를 그대로 복사해서 쓰면 된다.

```
현재 이 프로젝트의 추천 배치는 app/services/recsys/lightfm_model.py의 두 함수만으로
app/jobs/recsys/lightfm_pipeline.py와 연결되어 있다:

  train_lightfm_model(user_ids, item_ids, interactions, item_genre_tags) -> (model, dataset, item_feature_matrix)
  predict_scores_for_user(model, dataset, item_feature_matrix, user_id, excluded_movie_ids) -> list[LightfmScore(movie_id, score)]

이 두 함수의 시그니처와 반환 타입만 그대로 유지하면서, 내부 구현을 LightFM에서
{scikit-surprise 또는 implicit 중 지정}로 교체해라.

먼저 claude/docs/model_swap_guide.md를 전부 읽고 시작해라. 그 문서에 현재 아키텍처,
파일별 역할, 지켜야 할 인터페이스 계약, 라이브러리별 주의사항(아이템 피처 미지원 여부,
top-N 생성 방식, 인덱스 매핑 필요 여부), 검증 방법이 정리되어 있다.

규칙:
1. app/jobs/recsys/lightfm_pipeline.py, app/jobs/recsys/interaction_signals.py,
   app/jobs/recsys/scheduler.py, DB 스키마는 건드리지 마라 (import 경로 1줄, SOURCE 상수 문자열 제외).
2. requirements.txt와 Dockerfile은 새 라이브러리가 실제로 설치/컴파일되도록 필요한 만큼만 수정해라.
3. 코드 컨벤션: 변수/함수는 snake_case, 클래스는 PascalCase. 함수 위에는
   "# YYYY.MM.DD 이름" 형식 주석 + 함수 설명을 반드시 남기고, 수정할 때마다 날짜를 갱신해라.
4. 교체 후 claude/loss_function_comparison.py와 같은 패턴으로 새 모델의 NDCG@10을
   claude/data(ml-latest-small 248명)에서 실측하고, 기존 LightFM(WARP) 실측값 0.2031과
   비교한 표를 보고해라. 이론적 주장이 아니라 반드시 실행한 숫자로 근거를 대라.
5. docker build로 실제 배포 이미지가 빌드되는지, docker run으로 배치가 실제 DB에
   recommendations 행을 쓰는지까지 확인하고 보고해라.
```
