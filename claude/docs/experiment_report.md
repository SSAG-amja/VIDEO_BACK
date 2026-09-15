# Rule-based vs LightFM 추천 비교 실험 — 최종 리포트

> 관련 문서: [recsys_comparison_plan.md](recsys_comparison_plan.md)(사전 설계), [progress_log.md](progress_log.md)(작업 로그)
> 결과 파일: `claude/results/comparison_report.md`, `comparison_metrics.json`, `readable_*.csv`, `readable_comparison_sample.md`

---

## 1. 왜 이 실험을 했는가

프로젝트에는 이미 Rule-based 추천 시스템(`app/jobs/recsys/rec_pipeline.py`, `app/services/recsys/dynamic_retriever.py`)이 있다.
"LightFM으로 바꾸면 더 나을까?"를 코드만 보고 판단할 수 없어서, 실제로 두 방식을 **같은 유저·같은 영화·같은 평가 기준**으로 돌려서 숫자로 비교하기로 했다.

원칙(작업 시작 시 사용자가 명시):
- 결과가 나오기 전까지 어느 쪽이 낫다고 단정하지 않는다
- 기존 코드를 재구현(베껴 쓰기)하지 않고, 가능하면 실제 프로덕션 코드를 그대로 실행한다
- 코드 수정 전에 설계 문서부터 쓴다

---

## 2. 테스트를 어떻게 설계했는가, 왜 그렇게 했는가

### 2.1 데이터: 왜 MovieLens인가
프로젝트 실 서비스에는 아직 "평점" 데이터가 없다(Pin/Passed/Watched 같은 이진 신호만 있음). 두 알고리즘을 제대로 비교하려면 **한 유저가 시간순으로 여러 영화를 평가한 데이터**가 필요해서, 공개 데이터셋인 MovieLens(`ml-latest-small`, 유저 610명·평점 100,836건)를 사용했다.

### 2.2 평가 대상 유저: 왜 100개 이상 평점 유저만 쓰는가
평점이 너무 적은 유저는 학습/평가 분할을 해도 신호가 거의 없어 통계적으로 의미가 없다. 100개 이상 기준으로 걸러서 **248명**이 남았다 (610명 중).

### 2.3 학습/평가 분할: 왜 유저별 70:30인가
추천 시스템은 "과거를 보고 미래를 맞히는" 문제이므로, 무작위 분할이 아니라 **시간순 분할**이 맞다. 유저마다 활동 기간이 다르므로 분할 경계도 전체 데이터가 아니라 **유저별로 각자 계산**했다.

```
유저의 평점을 (timestamp, 원본 행 순서) 기준 정렬
→ 앞 70% = train, 뒤 30% = test
```

**주의사항 (실제로 확인하고 리포트에 남김):** timestamp가 같은 행이 여러 개면 실제 순서를 알 수 없다. 원본 CSV 행 순서를 보조 정렬 키로 썼지만, 이는 편의상 안정적인 처리 순서일 뿐 실제 행동 순서를 보장하지 않는다. 확인 결과 **29명의 유저**가 분할 경계에서 이 동률 문제의 영향을 받았고, 전체적으로 **5,551개 동률 그룹**이 있었다 (`claude/data/split_stats.json`).

### 2.4 평점 → 상태 매핑, 왜 이렇게 했는가

| 평점 | 상태 | 비고 |
|---|---|---|
| 0.5~1.5 | PASSED | 프로덕션의 `is_passed`에 대응 |
| 2.0~3.0 | NEUTRAL | 긍정도 부정도 아님 → 프로필 점수에 반영 안 함 |
| 3.5~4.5 | PINNED | 프로덕션의 `is_pinned`에 대응 |
| 5.0 | SAVED | **사용자 확인**: "playlist에 들어있으면 saved" → 프로덕션의 `is_pinned` 플래그가 아니라 실제 `playlists`/`playlist_movies` 테이블에 대응시킴 |

### 2.5 "재구현" 대신 "실제 코드 실행"으로 방향을 튼 이유
처음에는 MovieLens 데이터만으로 Rule-based 로직을 흉내 내서 재구현하려 했다(장르 정보만 있고 배우/감독/인기도가 없어서). 하지만 도중에 사용자가 "실제 운영 코드와 실제 LightFM 코드를 근거로 산출해야 한다"고 확인해줬고, 마침 로컬 Docker를 켤 수 있어서 방향을 바꿨다:

- 실제 DB(Postgres)를 Docker로 띄우고
- 실제 운영 데이터 시딩 스크립트(`app/seed_db.py`)로 **실제 영화 카탈로그 117만 6,546건**(장르/키워드/배우/감독 매핑 포함)을 로컬 DB에 채워 넣고
- MovieLens의 `movieId`를 실제 DB와 연결할 수 있는지 먼저 검증했다:
  - `links.csv`의 tmdbId ↔ 실제 `movies.tmdb_id` 매칭률 **98.8%** (9,616 / 9,734)
  - `Movie.id == Movie.tmdb_id`가 100% 일치 → 별도 매핑 테이블 없이 바로 연결 가능
  - 매칭된 영화들의 장르 99.99%, 키워드 96.1%, 감독 99.8%, 배우 99.7%가 이미 실제 데이터로 채워져 있음

이 검증 덕분에 "MovieLens 영화를 실제 DB의 진짜 영화로 취급해서, 실제 추천 코드를 그대로 돌려도 된다"는 결론이 나왔고, 재구현 계획을 접었다.

---

## 3. 어떻게 구현했는가, 왜 그렇게 했는가

모든 코드는 `app/jobs/recsys/evaluation/`에 새로 작성했고(기존 서비스 코드와 분리), 실험 산출물은 전부 `claude/`에 저장했다(`.gitignore`에 `claude/` 등록됨, git 추적 안 됨).

### 3.1 데이터 준비 — `data_prep.py`
2.2~2.4의 필터링/분할/매핑을 수행. 순수 pandas 함수라 DB 없이도 단위 테스트 가능하게 짬.

### 3.2 실제 DB에 MovieLens 유저 시딩 — `seed_real_db.py`
- MovieLens 248명을 실제 `users` 테이블에 더미 계정으로 생성 (실제 로그인에 안 쓰이므로 비밀번호는 더미 문자열)
- train 구간 상호작용을 실제 스키마로 변환해 저장:
  - 전부 `is_watched=True` (평점을 매겼다 = 봤다는 뜻)
  - PINNED → `is_pinned=True`, PASSED → `is_passed=True`
  - SAVED → `playlists` 생성 후 `playlist_movies`에 저장
- 매칭 안 되는 영화 68개는 제외
- **결과**: 유저 248명(db id 249~496), 상호작용 58,589건, SAVED→playlist_movies 7,060건
- 왜 이렇게 했는가: 되돌리기가 가능해야 해서 시딩한 모든 id를 `claude/data/seed_manifest.json`에 기록하고, `teardown_real_db.py`로 한 번에 삭제(CASCADE)할 수 있게 만듦

### 3.3 Rule-based 추천 뽑기 — `rule_based_eval.py` + `run_rule_based.py`
**재구현하지 않고** `rec_pipeline.py`의 실제 함수/상수를 그대로 import해서 사용:
`movie_score()`, `GENRE_MATCH_WEIGHT`(8.0)/`KEYWORD_MATCH_WEIGHT`(5.0)/`DIRECTOR_MATCH_WEIGHT`(6.0)/`ACTOR_MATCH_WEIGHT`(3.0), `build_preference_profile()`, `build_collaborative_candidates()`, `select_source_quotas()`, `allocate_candidate_counts()`.

**유일하게 바꾼 부분**: 콘텐츠/탐색(인기도) 후보를 뽑을 때 프로덕션은 실 서비스 카탈로그 117만 건 전체를 SQL로 스캔하는데, 이러면 LightFM(MovieLens 매칭 영화만 학습 가능)과 비교 기준이 달라진다. 그래서 SQL의 스캔 범위만 "MovieLens와 매칭되는 영화"로 제한했다. 점수 계산 공식 자체는 원본과 완전히 동일하다.

248명 전원 실행 → 251초 소요, 빈 결과 0건 → `claude/results/rule_based_candidates.json`

### 3.4 LightFM 학습 — `lightfm_eval.py`
- **환경 문제**: 로컬 Python 3.13에서 LightFM이 컴파일 안 됨 (LightFM의 C 확장 코드가 Python 3.12+에서 바뀐 내부 구조와 호환 안 되는 알려진 문제). 로컬 Python 3.11도 brew 설치가 깨져 있어서 못 씀.
  → 해결: 프로젝트가 실제 배포에 쓰는 `python:3.11-slim` Docker 이미지 안에서 LightFM을 설치·실행 (로컬 시스템은 전혀 건드리지 않음)
- 긍정 상호작용만 사용: rating ≥ 3.5(PINNED/SAVED), weight=rating — Rule-based가 양수 스코어만 프로필에 반영하는 것과 같은 원칙
- item_features: 실제 DB의 `movie_genres`를 그대로 읽어서 장르 원-핫 피처로 사용 (콘텐츠 신호를 대칭적으로 반영)
- WARP loss로 학습. **학습이 매번 조금씩 다르게 나오는 걸 확인하려고 seed 0~4로 5번 반복 학습**
- 학습 구간에 등장한 영화는 Rule-based와 동일하게 후보에서 제외
- 248명 × 5 seed → `claude/results/lightfm_candidates.json`

### 3.5 평가 지표 — `metrics.py`
직접 구현(수동 계산으로 검증 완료):
- Precision@K, Recall@K, NDCG@K, MAP@K (K=10, 20, 50)
- Coverage@50 (추천된 서로 다른 영화 수 / 전체 후보 카탈로그) — 소수의 인기작만 반복 추천하는지 확인용
- Bootstrap 95% 신뢰구간 (유저 248명을 복원추출 1,000회)
- Wilcoxon signed-rank 검정 (두 모델의 유저별 점수 쌍 비교, 통계적 유의성 확인)

### 3.6 최종 비교 — `run_comparison.py`
- 정답: test 구간(미래 30%)의 PINNED/SAVED 영화만 "진짜 좋아할 영화"로 취급
- Rule-based, LightFM(5-seed), **Popularity-only 베이스라인**, **Random 베이스라인**을 전부 같은 기준으로 채점
- 왜 베이스라인을 넣었는가: 베이스라인 없이 "LightFM이 이겼다"고만 말하면, 혹시 둘 다 인기 영화만 잘 맞힌 것뿐인지 구분이 안 된다. 실제로 이게 이번 실험에서 결정적인 역할을 했다 (아래 5장 참고).

### 3.7 사람이 읽는 결과물 — `export_readable.py`
숫자만으로는 "왜 이런 차이가 나는지" 감이 안 와서, 영화 제목까지 붙인 CSV(248명 전체)와 유저 10명 샘플 비교 markdown을 추가로 만들었다.

---

## 4. 어떤 결과가 나왔는가

### 4.1 정량 결과 (NDCG@10, 247명 평가 기준)

| 모델 | NDCG@10 | 비고 |
|---|---|---|
| **LightFM** (5-seed 평균 ± 표준편차) | **0.2043 ± 0.0031** | 가장 우수, seed 간 편차 매우 작음(안정적) |
| Popularity-only 베이스라인 | 0.1706 | 개인화 없이 "인기순"만으로도 상당히 강함 |
| Rule-based (실제 `rec_pipeline.py`) | 0.0303 | **인기순 베이스라인보다도 낮음** |
| Random 베이스라인 | 0.0104 | 최저 기준선 |

Wilcoxon signed-rank 검정(Rule-based vs LightFM, NDCG@10): **p < 0.0001** → 통계적으로 유의미한 차이.

전체 K값(10/20/50)별 Precision/Recall/MAP, Coverage는 `claude/results/comparison_report.md` 참고.

### 4.2 왜 Rule-based가 인기순보다도 낮은가 — 버그 검증 결과 (버그 아님)

숫자만 보고 "하네스가 잘못됐나?" 의심할 수 있어서 실제로 파고들었다:

- user_id=1을 직접 추적: Rule-based top-50이 **100% "content"(장르/배우/감독 매치) 소스**였고, 점수가 16804.2 같은 수천 단위였음. 반면 인기도 항목(`movie_score`)은 popularity×0.05+vote_average로 5~15 수준밖에 안 됨.
- 같은 유저의 정답(미래에 실제로 좋아한 영화)과 대조: Rule-based top-50은 **0건 적중**, LightFM은 **5건 적중**.
- 원인: `_accumulate_feature_candidates`의 `feature_scores[feature_id] * weight`(장르 8.0/키워드 5.0/감독 6.0/배우 3.0)가 유저의 과거 긍정 반응 수에 비례해 **상한 없이 누적**된다. 이번 실험에서 유저당 평균 상호작용이 ~114건이라, 콘텐츠 매치 점수가 인기도 항목을 수백~수천 배 압도해버린다.
- 결과적으로 "장르 하나, 배우 한 명만 겹쳐도 무조건 상위권"이 되어, 마이너하고 좁은 특징 공유 영화 위주로 추천이 쏠리고 인기도/협업 신호는 순위에 거의 영향을 못 준다.

**사람이 읽어도 확인되는 패턴** (`claude/results/readable_comparison_sample.md`, user_id=1):
- Rule-based top: *Wizards of Waverly Place*, *Hiding Out*, *The Amazing Screw-On Head* 같은 마이너작 위주
- LightFM top: *Shrek*, *Finding Nemo*, *반지의 제왕* 시리즈 같은 널리 사랑받는 작품 위주

**결론적으로 이건 "LightFM이 원래 더 똑똑해서"가 아니라, 현재 Rule-based의 콘텐츠 가중치가 정규화 없이 무한정 커질 수 있는 구조적 특성 때문이다.** 이 문제는 이론적으로 가중치를 정규화하거나 클리핑하면 개선될 여지가 있지만, 이번 실험에서는 "현재 로직을 건드리지 않는다"는 원칙에 따라 실제로 고쳐보지는 않았다.

### 4.3 참고: 콜드스타트/신규 영화 처리는 이번 실험 범위 밖
이번 비교는 "이미 상호작용 이력이 있는 기존 유저"에 대해서만 진행했다. 신규 유저 온보딩(`dynamic_retriever.py`)은 건드리지 않았고, LightFM은 학습 데이터에 없는 유저를 다루지 못하므로 실제 도입 시에도 온보딩 로직은 별도로 유지해야 한다.

---

## 5. 한계 및 다음에 논의할 것

- **동일 timestamp 정렬 이슈** (2.3) — 분할 경계 근처 유저 29명의 train/test 배정이 실제 행동 순서와 다를 수 있음
- **평가 유니버스가 전체 카탈로그가 아님** — MovieLens와 매칭되고 train에 등장한 영화 7,298개로 한정됨 (실 서비스 전체 117만 건 중 일부)
- **recency_decay가 실행 시점(2026년) 기준으로 계산됨** — MovieLens 상호작용이 전부 2018년 이전이라 전원 최저 감쇠계수(0.4)가 균일 적용됨. 유저 내 상대적 순위엔 영향이 적지만 절대 스코어 스케일에는 영향
- **콘텐츠 가중치 정규화 실험은 아직 안 함** — Rule-based를 고치면(가중치 상한/정규화) 결과가 달라질 수 있음, 별도 실험으로 가능
- **실서비스 연결(Phase 2)은 아직 안 함** — 이번 결과를 근거로 실제로 붙일지, 어떤 방식(협업 부분만 교체 vs 전체 교체)으로 할지는 사용자 판단 필요
- **시딩한 테스트 데이터(가짜 유저 248명)가 아직 DB에 남아있음** — `teardown_real_db.py`로 정리 가능, 아직 실행 안 함

---

## 6. 파일 위치 정리

| 파일 | 내용 |
|---|---|
| `claude/docs/recsys_comparison_plan.md` | 사전 설계 문서 |
| `claude/docs/progress_log.md` | 작업 진행 로그 (재현 순서 포함) |
| `claude/docs/experiment_report.md` | 본 문서 |
| `claude/data/train.csv`, `test.csv`, `split_stats.json` | 데이터 분할 결과 |
| `claude/data/seed_manifest.json` | DB 시딩 매핑 (되돌리기용) |
| `claude/results/rule_based_candidates.json` | Rule-based 추천 결과 (원본) |
| `claude/results/lightfm_candidates.json` | LightFM 추천 결과 (5-seed, 원본) |
| `claude/results/readable_rule_based.csv`, `readable_lightfm_seed0.csv` | 영화 제목 붙은 사람이 읽는 버전 |
| `claude/results/readable_comparison_sample.md` | 유저 10명 나란히 비교 |
| `claude/results/comparison_metrics.json`, `comparison_report.md` | 최종 지표/리포트 |
