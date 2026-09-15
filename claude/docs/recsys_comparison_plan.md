# Rule-based vs LightFM 추천 비교 실험 설계 문서

> 목적: 현재 서비스의 Rule-based 추천 로직과 LightFM 기반 추천을 **동일한 조건**에서 실행·평가하여, 실제 코드 교체 전에 근거를 확보한다.
> 이 문서 작성 시점까지는 코드를 수정하지 않았으며, 아래 계획에 대한 실험 산출물은 모두 `claude/` 아래에 생성한다 (git 추적 제외, `.gitignore`에 `claude/` 추가 완료).

---

## 1. 현재 시스템 분석 요약

### 1.1 추천이 호출되는 진입점

| 경로 | 파일 | 설명 |
|---|---|---|
| `GET /api/v1/explore/movies/recommended` | `app/api/v1/endpoints/explore.py:22-48` | `get_recommendations()` 호출, 실패 시 `movie_crud.get_recommended_movies()`(장르 기반)로 폴백 |
| `GET /api/v1/movie_load/shorts` | `app/api/v1/endpoints/movie_load.py:99-126` | 동일 함수 호출, 실패 시 TMDB popular 폴백 |
| `PUT /api/v1/user/user/favorite-movies` | `app/api/v1/endpoints/user.py:129-147` | 콜드스타트 — `build_cold_start_pool()` 호출 |
| 야간 배치 (스케줄러) | `app/jobs/recsys/scheduler.py` → `app/jobs/recsys/rec_pipeline.py:run_pipeline()` | 매일 06:00(Asia/Seoul) 전체 사용자 추천 재계산, `recommendations` 테이블에 저장 |

호출 흐름 (읽기 시점): `recommendation.py:get_recommendations()` → Redis 블랙리스트 + DB 제외 목록 로드 → `recommendations` 테이블에서 사전 계산된 후보 로드(`load_precomputed_candidates`) → 랭킹/필터링(`_rank_rows`) → 부족하면 `find_contextual_candidates`로 동적 보충 → 셔플 → 페이지네이션 응답.

배치 흐름(야간 갱신): `rec_pipeline.py:run_pipeline()` → Postgres advisory lock → `build_batch_context`(전체 유저 벡터 계산) → 유저별 `generate_user_candidates()` → 검증 → `recommendations` 테이블 전체 교체.

### 1.2 Rule-based 점수 계산식과 가중치

두 곳에 **거의 동일한 로직이 중복 구현**되어 있음 (야간 배치용 `rec_pipeline.py`, 콜드스타트용 `dynamic_retriever.py`).

기본 인기도 점수:
```
movie_score(m) = m.popularity * 0.05 + m.vote_average * 1.0
```

콘텐츠 후보 점수 (장르/키워드/감독/배우 매칭):
```
score = movie_score(m) + feature_scores[feature_id] * weight
GENRE_MATCH_WEIGHT = 8.0, KEYWORD_MATCH_WEIGHT = 5.0
DIRECTOR_MATCH_WEIGHT = 6.0, ACTOR_MATCH_WEIGHT = 3.0
```

사용자 행동 가중치 (`ACTION_WEIGHTS`):
```
passed=-3.0, pinned=4.0, watched=6.0,
playlist_add=6.0, post_write=7.0, like=1.0, reply=1.5
```
+ 최근성 감쇠: 30일 이내 ×1.0, 90일 ×0.8, 180일 ×0.6, 그 이후 ×0.4.

후보 풀 배분: content 60% / collaborative 20% / explore(인기도) 20%.
협업 필터링은 학습된 모델이 아니라 **유저-유저 코사인 유사도**(순수 계산, `rec_pipeline.py:733-743`)이다.

이 가중치들은 **`.env`/설정으로 외부화되어 있지 않고 두 파일에 하드코딩·중복**되어 있다는 점이 현재 구조의 약점이다.

### 1.3 사용자 행동 데이터 조회 위치

- `UserInteraction` (`app/models/mapping.py:73-86`, 테이블 `user_interactions`) — `is_pinned/is_watched/is_passed` + 각 타임스탬프. `pin` 설정 시 `pass`가 자동 해제되고 그 반대도 동일 (`app/crud/interaction.py:80-89`).
- 암묵적 참여 신호: 플레이리스트 추가, 게시글 작성, 좋아요, 댓글 (`rec_pipeline.py:287-382`, `Playlist/PlaylistMovie/Post/likes/Reply` 조인).
- Redis: 최근 행동 리스트 + 블랙리스트(제외 대상, TTL 30일) — 스코어링에는 쓰이지 않고 조회 시점 제외 필터로만 사용.

### 1.4 이미 본 영화 / Pin / Saved / Passed 처리 방식

- 단일 enum이 아니라 `user_interactions`에 **독립적인 3개의 boolean 플래그**로 표현됨. "Saved"라는 상태는 별도로 없고, **`playlist_movies` 테이블 소속 여부**로 표현된다(별도 개념).
- 피드 제외 조건: `is_watched OR is_passed` 만 제외. **`is_pinned`는 제외되지 않고 가중치만 가산**되며, 플레이리스트 저장(saved) 역시 제외되지 않고 긍정 신호로만 반영된다. 이는 실험 설계에서 "Saved=제외"로 오해하지 않도록 유의해야 하는 지점이다.

### 1.5 후보 생성 및 필터링

콘텐츠(장르/키워드/감독/배우 매칭 SQL 조인) + 협업(유저 벡터 코사인 유사도) + explore(전역 인기도) 세 소스를 쿼터별로 병합 → 중복 제거/부족분 보충 → `validate_candidates`(중복 id, NaN/Inf 점수, 존재하지 않는 movie_id, 최소 개수 100 미만이면 기존 추천 유지) 검증 후 DB 저장.

### 1.6 추천 결과 반환 형식

내부 서비스 반환 (`app/schemas/recsys.py:17-25`):
```python
class RecommendationResponse(BaseModel):
    user_id: int
    mode: RecommendationMode          # "all" | "subscribed_only"
    movie_ids: list[int]              # 내부 Movie.id, 랭킹 순서
    limit: int; offset: int; count: int; has_more: bool
    source: str                       # "precomputed" | "dynamic" | "mixed" | "empty"
```
DB 저장 스키마(`Recommendation`, `app/models/recommendations.py`): `user_id, movie_id` 복합 PK, `score, rank(유저별 unique), source, source_scores(JSON), created_at`.

### 1.7~1.8 MovieLens ID ↔ 프로젝트 Movie ID 연결

`Movie` 모델(`app/models/movie.py:5-38`): PK는 자체 `id`(내부 자동증가), 외부 ID로 `tmdb_id`(unique, index) / `imdb_id`(String, unique 제약 없음) 보유. **MovieLens 전용 컬럼은 없음.**

`ml-latest-small/links.csv`는 `movieId,imdbId,tmdbId` 매핑을 제공하므로, 이론적으로는 `links.tmdbId == Movie.tmdb_id`로 조인 가능하다. 단, 확인이 필요한 리스크:
- links.csv의 `imdbId`는 `tt` 접두어 없이 0-padding된 문자열(`0114709`)인 반면 `Movie.imdb_id`가 어떤 포맷으로 저장되는지 확인되지 않음 → **tmdbId 매칭을 우선 기준으로 사용**.
- 실제 프로젝트 DB(`movies` 테이블)에 MovieLens의 9,742개 영화 중 몇 개가 TMDB 동기화로 이미 존재하는지는 **DB 조회 없이는 알 수 없음** (이 실험은 오프라인 데이터로 진행하므로 실DB 접근을 필수로 하지 않음 — 아래 4장 참고).

### 1.9 모델 학습/추천 생성 배치 위치, 1.10 재사용 vs 신규 작성

아래 3장(아키텍처)에서 다룸.

---

## 2. 왜 이 실험은 "완전한 프로덕션 재현"이 아니라 "동일 조건 오프라인 비교"인가

MovieLens `ml-latest-small`은 `movieId, title, genres`만 제공하고, 배우/감독/키워드/popularity/vote_average 같은 프로덕션 스코어링에 필요한 필드가 없다. 두 가지 선택지가 있었다:

- **(A) MovieLens 데이터만으로 폐쇄된 오프라인 실험** — 두 알고리즘 모두 동일한 유저/아이템/피처 공간에서 실행. 재현성 100%, 프로덕션 DB에 손대지 않음.
- **(B) MovieLens 유저를 실제 프로덕션 `Movie` 테이블에 매핑해서 실제 서비스 코드(`rec_pipeline.py`)를 그대로 실행** — 매칭된 영화만 평가 가능(커버리지 부분적), 실 DB에 가상 유저/상호작용을 써야 함(리스크 있음).

**이 문서는 (A)를 1차 목표로 채택**한다. 이유:
1. 사용자 지시 원칙("결과가 실행되기 전 특정 모델이 우수하다고 단정하지 마라", "동일 조건에서 실행")을 지키려면 두 모델이 **정확히 같은 유저·아이템·학습/평가 분할**을 보게 해야 하는데, 실 DB는 keyword/actor/director 커버리지가 불균등해 조건이 흔들린다.
2. (업데이트) 현재 서비스가 실제로 운영 중이 아니므로 실 DB/실서비스에 LightFM을 연결하는 것 자체는 리스크 사유로 막을 필요가 없다고 확인함. 다만 지금 이 환경에서는 Docker 데몬이 꺼져 있고 `.env`의 `DB_HOST=db`가 compose 네트워크 전용 호스트명이라 **당장 실 DB에 접근할 수 없는 실무적 제약**이 있다 — 승인 문제가 아니라 인프라 기동 문제로 성격이 바뀌었다.
3. 따라서 (A)는 여전히 "완전히 통제된 동일 조건 비교"로서 1차 목표를 유지하고, (B)는 "승인 대기 선택 단계"가 아니라 **인프라(Docker/DB)가 뜨는 즉시 이어서 진행 가능한 Phase 2**로 재분류한다 (§6 참고).

즉, Rule-based 로직은 **MovieLens 데이터 위에서 프로덕션과 동일한 가중치·공식으로 재구현**하고(장르만 콘텐츠 신호로 사용, keyword/actor/director는 데이터가 없으므로 생략 — 이 역시 "동일 조건"이 되도록 LightFM 쪽도 장르 피처만 item-feature로 사용), LightFM도 같은 학습/평가 분할로 학습한다.

---

## 3. 아키텍처 설계 — 어디에 무엇을 둘 것인가

```
app/jobs/recsys/evaluation/          <- 신규. 오프라인 비교 실험 전용, 기존 서비스와 분리
    __init__.py
    data_prep.py        # ml-latest-small 로드/필터/유저별 70:30 분할/등급→상태 매핑
    rule_based_replay.py# 프로덕션 가중치를 그대로 재사용한 MovieLens 버전 스코어러
    lightfm_model.py    # LightFM 학습/추론 wrapper (재사용 가능하게 설계)
    metrics.py           # Precision@K/Recall@K/NDCG@K
    run_comparison.py   # CLI 진입점: 위 전부 조합해 claude/results/*, claude/data/*에 출력
claude/
    docs/recsys_comparison_plan.md   # 본 문서
    data/                             # 분할된 train/test, 매핑 통계 (git 제외)
    results/                          # 지표, 비교 리포트 (git 제외)
    logs/                             # 실행 로그 (git 제외)
```

**재사용:** `app/jobs/recsys/rec_pipeline.py`의 `movie_score`, `ACTION_WEIGHTS`, `GENRE_MATCH_WEIGHT`, `recency_decay`, `cosine_similarity` — 이 상수/함수들은 값 자체를 그대로 import 해서 "동일 조건"을 보장한다 (값을 다시 베껴 적지 않고 원본 상수를 참조 → 나중에 프로덕션 값이 바뀌어도 실험이 자동으로 최신값을 따라감).

**신규 작성:** DB(SQLAlchemy Session) 의존성을 제거한 순수 함수 버전의 콘텐츠/협업 후보 생성 로직 (MovieLens는 Postgres가 아니라 CSV이므로), LightFM 학습 파이프라인, 평가지표.

**모델 학습·추천 생성 배치 계층:** 오프라인 비교 단계에서는 `app/jobs/recsys/evaluation/`(배치 job과 동급 계층)에 둔다. 추후 LightFM을 실제 서비스에 연결하기로 결정되면(§6), `lightfm_model.py`의 학습/추론 wrapper를 `app/services/recsys/`로 승격하고 `rec_pipeline.py`의 `build_collaborative_candidates`를 대체하는 방식이 가장 자연스럽다 — 지금 이 경계를 분리해두는 이유이기도 하다.

---

## 4. 2단계 — 데이터 정의 설계

### 4.1 평가 대상 사용자 필터링

```
ratings.csv (610 유저, 100,836건) 중 평점 개수 >= 100인 유저만 사용
→ 실측: 248명 (전체 610명 중)
```

### 4.2 유저별 시간순 정렬 및 70:30 분할

```python
rows = ratings[ratings.userId == u].reset_index()  # 원본 행 순서를 보조키로 보존
rows = rows.sort_values(by=["timestamp", "index"])  # timestamp 동률은 원본 행 순서로 tie-break
split_idx = floor(len(rows) * 0.7)
train = rows.iloc[:split_idx]
test  = rows.iloc[split_idx:]
```
- 경계는 **유저별로 개별 계산** (전체 데이터셋 기준이 아님).
- **주의사항(결과에 명시할 내용):** timestamp가 동일한 두 행은 실제 클릭/평가 순서를 보장하지 않는다. 원본 CSV 행 순서를 보조 정렬 기준으로 쓰지만, 이는 "안정적인 처리 순서"일 뿐 실제 행동 순서의 근사치가 아닐 수 있다. 최종 리포트에 동률 그룹 수와 영향받은 유저 수를 카운트해 명시한다.

### 4.3 평점 → 상태 매핑

```
0.5, 1.0, 1.5  → PASSED
2.0, 2.5, 3.0  → NEUTRAL
3.5, 4.0, 4.5  → PINNED
5.0            → SAVED
```

### 4.4 상태 → 프로덕션 가중치 매핑 (신규 결정 필요 지점)

프로덕션의 `ACTION_WEIGHTS`에는 `passed/pinned/watched`만 있고 `saved`라는 별도 항목이 없다(saved는 실제로는 playlist 소속 여부). MovieLens에는 "재생목록 저장" 개념이 없으므로, 이 실험에서는 아래와 같이 근사한다 — **이 결정을 문서로 남기는 이유는, 나중에 왜 이 숫자를 썼는지 추적 가능하게 하기 위함**:

| 상태 | 가중치 | 근거 |
|---|---|---|
| PASSED | `ACTION_WEIGHTS["passed"]` = -3.0 | 그대로 재사용, 피드 제외 대상 |
| NEUTRAL | 0.0 | 긍정도 부정도 아님 → 프로필에 반영 안 함(`build_preference_profile`이 `score <= 0`이면 스킵하는 것과 동일 원칙) |
| PINNED | `ACTION_WEIGHTS["pinned"]` = 4.0 | 그대로 재사용 |
| SAVED | `ACTION_WEIGHTS["playlist_add"]` = 6.0, `log2(1+units)` 적용(units=1 → 6.0×log2(2)=6.0) | (수정) 사용자 확인: "playlist에 들어있으면 saved" → 프로덕션에서 saved는 별도 플래그가 아니라 `playlist_movies` 소속 여부이므로, `watched` 채널이 아니라 **`playlist_add` 채널**로 모델링. 수치는 우연히 6.0으로 동일하지만 근거가 다르며, `PLAYLIST_DERIVED_SIGNAL_MULTIPLIER`는 적용하지 않음(직접 소유 플레이리스트 추가와 동급이지 파생 신호가 아니므로) |

- **피드 제외(exclude_from_feed) 처리:** 프로덕션은 `is_watched OR is_passed`만 제외한다. 그러나 MovieLens에서는 "평점을 매겼다"는 사실 자체가 이미 시청했음을 의미하므로, **학습 구간(train)에 등장한 모든 영화는 상태와 무관하게 해당 유저의 추천 후보 풀에서 제외**한다 (이는 곧 `watched=True`로 간주하는 것과 동일). PASSED/NEUTRAL/PINNED/SAVED 라벨은 오직 선호도 점수 계산에만 사용된다.
- 최근성 감쇠 기준 시각(`now`)은 실행 시점(2026년)이 아니라 **유저별 train 구간의 마지막 timestamp**로 둔다 (실행 시점 기준이면 오래된 MovieLens 데이터가 전부 최저 감쇠 구간으로 뭉개져 무의미해짐).

### 4.5 콘텐츠 피처

MovieLens `movies.csv`의 `genres` 파이프(`|`) 구분 목록만 사용 (keyword/actor/director는 데이터 없음 → 두 모델 모두 동일하게 생략, "동일 조건" 유지). `GENRE_MATCH_WEIGHT=8.0`을 그대로 재사용.

### 4.6 인기도/평점 프록시 (leakage 방지)

프로덕션 `movie_score = popularity*0.05 + vote_average*1.0`을 재현하려면 popularity/vote_average가 필요하나 MovieLens에는 없음. **train 데이터에서만** 계산한 프록시를 사용(테스트 구간 정보가 새어 들어가지 않도록):
```
popularity_proxy(movie) = train 내 해당 영화 평점 개수
vote_average_proxy(movie) = train 내 해당 영화 평점 평균 (없으면 0)
```

### 4.7 후보 유니버스

두 모델 모두 `movies.csv` 전체 9,742개 영화를 후보 유니버스로 삼는다 (4.4의 학습구간-제외 규칙만 유저별로 다르게 적용).

### 4.8 평가 지표 (강화)

Test 구간의 **PINNED 또는 SAVED**(rating ≥ 3.5) 항목을 "정답 긍정 아이템"으로 삼는다. PASSED/NEUTRAL은 정답 집합에 포함하지 않는다(정답이 아니라 "본 적 있음" 정도의 신호이므로).

K ∈ {10, 20, 50}에 대해 아래를 계산한다:
- **Precision@K / Recall@K / NDCG@K** — 기본 랭킹 품질
- **MAP@K** — 정답이 리스트 상단에 몰려 있을수록 높은 점수, NDCG와 상호 검증용
- **Coverage** — (전체 유저에게 추천된 서로 다른 영화 수) / (전체 카탈로그 9,742) — 모델이 소수의 인기작만 반복 추천하는지 확인. 이게 낮으면 Precision이 높아도 실제 서비스 품질로는 의미가 퇴색됨.

### 4.8b 베이스라인 (필수)

두 모델의 점수만 비교하면 "둘 다 인기 영화만 잘 맞혔다"는 가능성을 배제할 수 없다. 아래 두 베이스라인을 **반드시 함께 리포트**한다:
- **Popularity-only**: 개인화 없이 §4.6의 `popularity_proxy`(train 내 평점 개수) 순위로만 추천.
- **Random**: 후보 유니버스에서 무작위 K개.

Rule-based/LightFM이 Popularity-only를 유의미하게 이기지 못한다면, "더 나은 모델"이 아니라 "인기도를 재발견한 모델"이라는 뜻이므로 결론에 반드시 명시한다.

### 4.8c 통계적 검증 (강화)

- **유의성 검정**: 유저별 NDCG@10 값을 모델별로 쌍으로 만들어 **Wilcoxon signed-rank test** 수행 — 평균 차이가 통계적으로 유의한지, 소수 유저의 극단값 때문인지 구분.
- **LightFM 분산 처리**: WARP 학습은 난수 초기화/네거티브 샘플링에 의존하는 확률적 과정이므로 **서로 다른 seed로 5회 반복 학습**하고 평균±표준편차로 리포트. 단일 실행 결과만으로 우열을 단정하지 않는다는 작업 원칙과 직결.
- **Bootstrap 신뢰구간**: 248명 유저를 복원추출로 1,000회 리샘플링하여 각 지표의 95% CI 산출. 추가 데이터 분할 없이 저비용으로 "이 결과가 이 248명에게 우연히 나온 것인지"를 검증.

### 4.9 LightFM 학습 설계

- 상호작용 행렬: train 중 **rating ≥ 3.5**(PINNED/SAVED)인 (user, movie) 쌍만 긍정 상호작용으로 사용, weight=rating. Rule-based가 양수 스코어만 프로필에 반영하는 것과 동일한 원칙.
- item_features: 장르 원-핫 (Rule-based가 장르만 콘텐츠 신호로 쓰는 것과 대칭) → hybrid LightFM.
- loss: WARP (implicit ranking 표준 선택, 근거: 명시적 평점 회귀가 아니라 Top-K 랭킹 성능을 비교하는 실험 목적에 부합).
- 학습/평가에 사용하는 유저·아이템 ID는 4.1~4.7의 동일 분할·동일 후보 유니버스를 그대로 사용.

### 4.10 산출물

```
claude/data/train.csv, test.csv           # 유저별 분할 결과 (원본 컬럼 + status 컬럼 추가)
claude/data/split_stats.json              # 유저 수, 동률 tie-break 발생 건수, 유저별 train/test 크기
claude/results/metrics_rule_based.json
claude/results/metrics_lightfm.json
claude/results/comparison_report.md       # 지표 비교표 + tie-break 경고 문구 포함
```

---

## 5. 의존성 / 리스크

- 추가 필요 패키지: `lightfm`, `scipy`, `numpy`, `scikit-learn`(분할/지표 유틸, 선택). 현재 `requirements.txt`에는 없음.
- `lightfm`은 C-extension 빌드가 필요해 Python 3.11 + 최신 numpy 조합에서 빌드 실패 사례가 있음 → 설치 단계에서 실패 시 `--no-build-isolation` 또는 대안으로 `implicit` 라이브러리 사용을 검토 (본 실험에서 결정 필요 시 별도 확인).
- 기존 테스트 코드/pytest 설정이 프로젝트에 전혀 없음 → 이번에 `claude/` 또는 `app/jobs/recsys/evaluation/` 범위에 한해 최소한의 pytest 유닛 테스트를 신규로 추가 (등급→상태 매핑, 70:30 분할 경계, tie-break, precision/recall/ndcg 계산 검증).

## 6. Phase 2 — 실서비스 연결 (승인됨, 인프라 기동 대기)

서비스가 실제로 운영 중이지 않으므로 실 DB/실서비스에 LightFM을 연결하는 것 자체는 이제 리스크 사유로 막지 않는다. 다만 현재 세션에서는 Docker 데몬이 내려가 있어 즉시 진행할 수 없다 — **승인 문제가 아니라 인프라 기동 문제**로 확정한다. Docker/DB가 뜨면 아래 순서로 이어간다:

1. `docker-compose up -d db`(또는 전체 스택) 후, `links.csv` tmdbId 기준으로 실제 `movies` 테이블과 매칭되는 영화 비율 조사 — 매칭률이 낮으면(예: keyword/actor/director 커버리지 부족) Tier B 비교의 유효성이 제한적임을 그 자리에서 판단.
2. 매칭률이 충분하면, MovieLens 유저를 실 DB 스키마에 맞춰 시딩(별도 테스트 스키마 또는 격리된 DB 권장, 실 사용자 데이터와 섞이지 않도록)하고 `rec_pipeline.py`/`dynamic_retriever.py`를 **그대로** 실행해 Tier A 결과와 교차 검증.
3. Tier A 결과가 LightFM 우위를 뒷받침하면, `lightfm_model.py`의 학습/추론 wrapper를 `app/services/recsys/`로 승격하고 `rec_pipeline.py`의 협업 필터링(`build_collaborative_candidates`, 현재는 유저-유저 코사인 유사도)을 LightFM 기반 스코어러로 교체.
4. 이 단계는 실 DB 스키마/설정값 변경을 동반할 수 있으므로, 착수 직전 진행 방식(격리 스키마 vs 기존 DB에 테스트 유저 삽입)을 한 번 더 확인한다.

---

## 7. 실행 순서 체크리스트

1. [x] `claude/docs/recsys_comparison_plan.md` (본 문서)
2. [ ] `data_prep.py` 구현 → `claude/data/` 산출 (100+ 평점 필터, 유저별 70:30 분할, 등급→상태 매핑, tie-break 로깅)
3. [ ] `rule_based_replay.py` 구현 (프로덕션 상수 import, SAVED=`playlist_add` 채널)
4. [ ] `lightfm_model.py` 구현 (의존성 설치 확인 필요, seed 5개 반복 학습 지원)
5. [ ] `metrics.py` 구현 (Precision/Recall/NDCG/MAP@{10,20,50}, Coverage, Popularity-only/Random 베이스라인, Wilcoxon 검정, Bootstrap CI) + 최소 유닛 테스트
6. [ ] `run_comparison.py`로 양쪽 모델 + 베이스라인을 동일 분할 위에서 실행 → `claude/results/comparison_report.md`
7. [ ] 결과를 바탕으로 우열 판단 (실행 전 단정 금지 원칙 준수, Popularity-only 대비 유의미한 개선인지 필수 확인)
8. [ ] (Phase 2, 인프라 기동 후) §6 절차대로 실 DB tmdb_id 매칭률 조사 및 실서비스 교차 검증
