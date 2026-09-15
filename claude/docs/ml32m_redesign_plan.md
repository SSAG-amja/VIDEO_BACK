# ml-32m 기반 재설계 — 구조 분석 및 계획

> 이전 실험(`experiment_report.md`)은 `ml-latest-small`(248명) 기준이었다. 이 문서는
> `./ml-32m` 폴더를 기준으로 무엇이 다르고, 무엇을 재사용하고, 무엇을 바꿀지 정리한다.
> **아직 구현하지 않음 — 분석/설계만.**

## 1. `./ml-32m` 폴더 구조 분석 (실제 확인한 사실)

이 폴더는 **MovieLens 32M 전체가 아니라, 이미 사전 필터링된 10명 유저 고정 벤치마크**다.

```
ml-32m/
  filtered_links.csv    (1287줄 = 1286개 영화)  movieId,imdbId,tmdbId,videoBackMovieId,mappingMethod
  filtered_ratings.csv  (2118줄 = 2117개 평점)   userId,movieId,rating,timestamp
  test_ids.csv          (10명 유저의 train/test 개수 + 상태별 개수 미리 계산됨)
  test_ids_manifest.txt (seed=20260728, Users=10, Ratings=2117, Movies=1286)
```

- 원본 32M 규모 raw 파일(movies.csv, ratings.csv 등 수백만 행)은 **어디에도 없음** — Downloads 전체를 뒤져도 이 필터링된 4개 파일과 원본 zip(`ml-32m-test-10.zip`)만 존재.
- `filtered_links.csv`의 `videoBackMovieId`는 이미 우리 프로젝트 실제 `Movie.id`로 매핑되어 있음 (`mappingMethod=tmdb`가 1286개 전부).

## 2. 검증한 사실 (신뢰 가능한 이유)

1. **매핑 검증**: `videoBackMovieId` 1286개 전부(100%) 로컬 DB `movies` 테이블에 실존 확인함 (`SELECT count(*) FROM movies WHERE id = ANY(...)` → 1286/1286). 지난번 `ml-latest-small`은 98.8%였는데, 이번 건 이미 필터링되어 100%.
2. **분할/상태 로직 재현 검증**: 우리가 쓴 것과 동일한 방법론(유저별 timestamp+행순서 정렬 → 70:30 분할, 0.5~1.5=PASSED/2~3=NEUTRAL/3.5~4.5=PINNED/5.0=SAVED)을 `filtered_ratings.csv`에 직접 적용해봤더니, `test_ids.csv`의 모든 숫자와 **정확히 일치**했다 (예: user 35015 → train=150/test=65, train_pinned=84/saved=7/passed=15, 전부 일치). 즉 이 벤치마크는 우리 설계 문서(§4)와 동일한 방법론으로 만들어졌다는 뜻이고, 우리 코드를 그대로 재사용해도 된다.
3. `test_relevant`/`test_negative`의 정의도 역산 검증됨: `test_relevant = test 구간의 PINNED+SAVED`, `test_negative = test 구간의 PASSED+NEUTRAL`.

## 3. 이전 실험과 다른 점

| 항목 | 이전(ml-latest-small) | 이번(ml-32m 벤치마크) |
|---|---|---|
| 유저 수 | 248명 | **10명** |
| 영화 수 | 9,616개 (매칭) → 7,298개 (train 등장) | 1,286개 |
| 평점 수 | ~84,000건 | 2,117건 |
| 영화 ID 매핑 | 직접 links.csv 조인 + DB 존재 확인 필요 | **이미 매핑 완료됨** (videoBackMovieId 그대로 사용) |
| 성격 | 통계적 비교(부트스트랩 CI, Wilcoxon 유의미) | **고정 소규모 벤치마크** — 유저 10명으론 통계 검정 의미가 약함 |

10명 규모라 이번엔 "정량 비교/유의성 검정"보다 **"두 모델이 실제로 어떤 영화를 추천하는지 리스트 자체를 뽑아서 보는 것"**에 맞는 스케일이다. 사용자가 요청한 2단계 계획과도 일치한다.

## 4. 재사용 vs 변경

**그대로 재사용** (경로 상수만 분리하면 로직 변경 없이 동작):
- `seed_real_db.py` / `teardown_real_db.py` — 유저/상호작용 시딩 구조 동일
- `rule_based_eval.py` / `run_rule_based.py` — 프로덕션 함수 재사용 방식 동일
- `lightfm_eval.py` — WARP 학습 방식 동일 (유저 10명이라 학습 시간은 훨씬 짧아짐)
- `export_readable.py` — 사람이 읽는 CSV/비교 markdown 생성 동일

**바꿔야 하는 부분**:
1. `data_prep.py`의 입력을 `ml-32m/filtered_ratings.csv`로, 출력 디렉터리를 `claude/data_ml32m/`처럼 분리 (이전 `claude/data/`의 248명 결과와 안 섞이게)
2. movieId → Movie.id 매핑: `filtered_links.csv`의 `videoBackMovieId`를 바로 사용 (별도 `links.csv` 조인/DB 존재 확인 불필요, 이미 100% 검증됨)
3. 결과 저장 경로도 `claude/results_ml32m/`처럼 분리해서 이전 실험 결과와 섞이지 않게 함

## 5. 이번에 할 일 (사용자가 요청한 2단계만)

```
1단계: 기존 Rule-based 모델로 이 10명의 추천 리스트 뽑기
2단계: LightFM 모델로 같은 10명의 추천 리스트 뽑기
```
지표 비교/베이스라인/유의성 검정은 **이번 설계에 포함하지 않음** (2단계까지 뽑아서 먼저 확인한 뒤 사용자가 다음을 지시).

## 6. 진행 전 확인 필요한 것 (열린 질문)

- 이전 `ml-latest-small` 실험 때 시딩한 가짜 유저 248명(db id 249~496)이 아직 DB에 남아있음. userId 범위가 겹치지 않아(MovieLens 32M 실제 userId는 35015 등) 새로 시딩해도 충돌은 없지만, **정리하고 새로 시작할지 그대로 둘지** 확인 필요.
- `.venv_lightfm/`는 삭제하려 했으나 안전 설정(`Bash(rm -rf*)` deny 규칙)에 막힘 — 그대로 둠, 필요하면 규칙을 조정하거나 직접 삭제.
