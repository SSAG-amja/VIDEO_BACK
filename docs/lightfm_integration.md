# LightFM 추천 모델 연동 (2026.07.28)

## 무엇을 바꿨는가

야간 배치가 추천 후보를 계산하는 방식을 Rule-based(콘텐츠+협업+탐색 블렌드)에서
**LightFM(WARP loss 협업 필터링)**으로 완전히 교체했다. Rule-based 로직(`rec_pipeline.py`)과
그 비교 평가에만 쓰이던 평가 하네스(`app/jobs/recsys/evaluation/`)는 더 이상 필요하지 않아
삭제했다. 두 방식 모두에서 공통으로 쓰던 "유저 상호작용 신호 추출 + 배치 중복 실행 방지 락"
로직만 `interaction_signals.py`로 분리해 남겼다.

| 파일 | 상태 | 내용 |
|---|---|---|
| `app/jobs/recsys/interaction_signals.py` | 신규 | `rec_pipeline.py`에서 알고리즘과 무관한 부분만 분리: `load_interaction_signals`(pinned/watched/passed + 플레이리스트/게시글/좋아요/댓글 파생 신호), `acquire_worker_lock`/`release_worker_lock` |
| `app/services/recsys/lightfm_model.py` | 신규 | LightFM 학습(`train_lightfm_model`)/추론(`predict_scores_for_user`). DB 세션을 열지 않는 순수 로직 |
| `app/jobs/recsys/lightfm_pipeline.py` | 신규 | 배치 오케스트레이션. `interaction_signals.py`의 락/신호 함수를 재사용 |
| `app/jobs/recsys/scheduler.py` | 수정 (1줄) | `from app.jobs.recsys.rec_pipeline import run_pipeline` → `from app.jobs.recsys.lightfm_pipeline import run_pipeline` |
| `requirements.txt` | 수정 (1줄) | `lightfm` 추가 |
| `app/core/config.py` | 수정 (2줄 삭제) | Rule-based 배치 전용이었던 `WORKER_MIN_CANDIDATE_COUNT`/`WORKER_RETRY_ATTEMPTS` 삭제 (LightFM 배치는 후보 수 최소값 검증/재시도 로직이 없음) |
| `app/jobs/recsys/rec_pipeline.py` | **삭제** | Rule-based 콘텐츠+협업+탐색 블렌드 로직. 프로덕션에서 더 이상 호출되지 않고, 유일한 사용처였던 평가 하네스도 함께 삭제되어 완전히 제거 |
| `app/jobs/recsys/evaluation/` | **삭제** | Rule-based ↔ LightFM 비교 평가용 스크립트 일체. 모델 교체 결정은 이미 내려졌고 프로덕션 코드가 아니므로 제거 |
| `Dockerfile` | 수정 (1줄) | `build-essential` 추가 — lightfm의 Cython 확장 빌드에 gcc 필요 |

**건드리지 않은 것**: `app/services/recsys/recommendation.py`(읽기 API, 무수정), `app/services/recsys/dynamic_retriever.py`(콜드스타트 온보딩, 무수정), `app/api/v1/endpoints/*`(엔드포인트 코드, 무수정).

## 왜 이렇게 설계했는가

1. **읽기 API를 안 건드려도 되는 이유**: `recommendation.py:get_recommendations()`는 `recommendations` 테이블을 그냥 읽어서 정렬/필터링만 한다 (누가 채웠는지 모름). 그래서 배치가 이 테이블에 뭘 채우든 API 코드는 무수정으로 그대로 동작한다. `Recommendation.source` 컬럼에 `"lightfm"`이 찍히는 것 말고는 API 응답 스키마도 그대로다.
2. **콜드스타트는 왜 그대로 뒀는가**: LightFM은 학습 데이터(상호작용 이력)가 없는 신규 유저의 벡터를 만들 수 없다. 그래서 `load_worker_user_ids()`로 상호작용 이력이 있는 유저만 이 배치 대상으로 삼고, 신규 유저는 계속 기존 `dynamic_retriever.py`의 장르 기반 온보딩 로직이 담당한다.
3. **신호(가중치) 추출을 재구현하지 않은 이유**: `interaction_signals.load_interaction_signals()`가 이미 pinned/watched/passed + 플레이리스트/게시글/좋아요/댓글 파생 신호 + 최근성 감쇠까지 전부 계산해준다(원래 `rec_pipeline.py`에 있던 로직을 알고리즘 비의존 부분만 이 모듈로 분리한 것). 이걸 그대로 가져다 쓰고 `score > 0`인 것만 LightFM 긍정 상호작용으로 넘긴다. 즉 "무엇을 좋아하는지 판단하는 기준"은 하나도 안 바뀌었고, "그 판단으로 무엇을 학습하는지"만 바뀐 것이다.
4. **아이템 유니버스를 왜 5,000개로 제한했는가 (`ITEM_POOL_SIZE`)**: 실 카탈로그가 117만 건이 넘는다. 유저마다 117만 개 아이템 전체에 대해 LightFM 점수를 매기는 건 비효율적이다. 인기도 상위 5,000개로 먼저 좁히고(검색/retrieval 단계) 그 안에서 LightFM으로 순위를 매기는(랭킹 단계) 2단계 구조를 썼다. **트레이드오프**: 인기 상위 5,000위 밖의 롱테일 영화는 유저가 그 영화와 상호작용한 이력이 있어도 이번 배치의 추천 후보에 못 들어간다. 이건 의도적으로 남겨둔 단순화이며, 필요하면 `ITEM_POOL_SIZE`를 늘리거나 "유저가 상호작용한 영화는 무조건 포함" 규칙을 추가하는 식으로 나중에 개선할 수 있다.
5. **모델을 왜 매번 다시 학습하고 저장 안 하는가**: 기존 Rule-based 배치도 매일 밤 전체를 재계산하는 방식이었다. 같은 운영 패턴을 유지해 배포/운영 복잡도를 늘리지 않았다. 학습 비용이 문제가 되면 `assets/ml_models/`에 모델을 저장해 재사용하는 걸 다음 단계로 고려할 수 있다 (README에 메모해둠).

## 어떻게 추천 리스트를 뽑는가 (최종 사용법)

### 1) 배치를 수동으로 한 번 돌려서 확인하고 싶을 때
```bash
docker compose up -d db redis          # DB/Redis 켜기
docker build -t pinlm-back .           # 실제 Dockerfile로 이미지 빌드 (lightfm 포함)
docker run --rm --network video_back_default \
  --env-file .env -e DB_HOST=db -e DB_PORT=5432 -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -w /back pinlm-back python -m app.jobs.recsys.lightfm_pipeline
```
`recommendations` 테이블에 `source="lightfm"`으로 유저별 추천이 채워진다.

### 2) 실제 서비스처럼(스케줄러로) 매일 자동 실행하고 싶을 때
```bash
docker run -d --network video_back_default \
  --env-file .env -e DB_HOST=db -e DB_PORT=5432 -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -w /back pinlm-back python -m app.jobs.recsys.scheduler
```
`settings.WORKER_SCHEDULE_HOUR/MINUTE`(기본 06:00 Asia/Seoul)에 맞춰 `lightfm_pipeline.run_pipeline()`이 자동 호출된다.

### 3) 특정 유저의 추천 리스트를 코드에서 직접 뽑고 싶을 때
```python
from app.db.session import SessionLocal
from app.services.recsys.recommendation import get_recommendations, RecommendationOptions
from app.schemas.recsys import RecommendationMode

with SessionLocal() as db:
    result = get_recommendations(
        db, redis_client, settings,
        RecommendationOptions(user_id=123, mode=RecommendationMode.ALL, limit=20),
    )
    print(result.movie_ids)  # LightFM 배치가 채워둔 recommendations 테이블 기준 결과
```
이 코드는 배치 교체 전과 완전히 동일하다 — API/서비스 계층은 안 바뀌었기 때문이다.

### 4) 실제 API로 확인하고 싶을 때
```
GET /api/v1/explore/movies/recommended
GET /api/v1/movie_load/shorts
```
둘 다 무수정이며, 배치가 LightFM으로 채운 `recommendations` 테이블을 그대로 서빙한다.

## 최종 검증 결과 (2026.07.28)

리팩터링(신호 로직 분리 → `interaction_signals.py`, `rec_pipeline.py`/`evaluation/` 삭제, `config.py` 정리, `Dockerfile` 수정) 이후 실제 배포 이미지 기준으로 다시 검증했다.

| 항목 | 방법 | 결과 |
|---|---|---|
| 실제 Dockerfile로 이미지 빌드 | `docker build -f Dockerfile .` | 성공 — `build-essential` 추가 후 `lightfm-1.17` 정상 컴파일 |
| 전체 앱 import 무결성 | 빌드된 이미지 안에서 `app.main`, `scheduler`, `lightfm_pipeline`, `interaction_signals`, `lightfm_model`, `recommendation`, `dynamic_retriever` import | 전부 성공 — `rec_pipeline.py` 삭제 후 깨진 참조 없음 |
| 배치 재실행 | 같은 이미지에서 `python -m app.jobs.recsys.lightfm_pipeline` | `users=10 items=5000 interactions=1243` 학습, 10명 전원 갱신 |
| DB 상태 | `SELECT source, count(*), count(DISTINCT user_id) FROM recommendations GROUP BY source;` | `source='lightfm'` 5,000행(10명), rule-based 잔여 데이터 없음 |

## 테스트(검증) 방법

**A. 배치가 정상 도는지**
```bash
docker run --rm --network video_back_default --env-file .env \
  -e DB_HOST=db -e DB_PORT=5432 -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -w /back pinlm-back python -m app.jobs.recsys.lightfm_pipeline
```
로그에 `lightfm model trained users=... items=... interactions=...` / `lightfm pipeline finished users=... replaced=...`가 찍히면 정상.

**B. DB에 실제로 반영됐는지**
```sql
SELECT source, count(*), count(DISTINCT user_id) FROM recommendations GROUP BY source;
```
`source='lightfm'` 행만 있고 `count(*) = 유저 수 × RECOMMENDATION_POOL_SIZE(기본 500)`이면 정상.

**C. API로 실제 추천 리스트 확인**
```
GET /api/v1/explore/movies/recommended
```
FastAPI 서버(`uvicorn app.main:app`)를 띄운 뒤 로그인 유저로 호출 — 배치가 채운 `recommendations`를 그대로 서빙하므로 응답 안에 LightFM이 뽑은 영화들이 나오면 정상.

**D. import 무결성 (리팩터링 후 깨진 참조 없는지)**
```bash
docker run --rm --network video_back_default --env-file .env \
  -e DB_HOST=db -e DB_PORT=5432 -e REDIS_HOST=redis -e REDIS_PORT=6379 \
  -w /back pinlm-back python -c "import app.main; import app.jobs.recsys.scheduler; print('OK')"
```

## 남은 작업

- 로컬(Docker 밖) 개발 환경에서 이 배치를 직접 실행하려면 로컬 Python 버전에서 LightFM이 컴파일되는지 먼저 확인 필요 (이번 세션에서 로컬 Python 3.13은 LightFM 컴파일이 안 됐고, Docker의 Python 3.11에서는 문제없이 됨을 확인함).
- `.env`의 `WORKER_MIN_CANDIDATE_COUNT`, `WORKER_RETRY_ATTEMPTS`는 Rule-based 배치 전용이던 값으로 이제 미사용이다 (config.py에서도 제거됨). `extra="ignore"` 설정이라 당장 문제는 없지만 정리하려면 `.env`에서 직접 삭제하면 된다.
