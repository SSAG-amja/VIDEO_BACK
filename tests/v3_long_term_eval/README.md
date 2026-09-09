# V3 장기 취향 NDCG 평가

이 디렉터리는 운영 코드와 아티팩트를 변경하지 않고 V3 장기 취향 추천을 오프라인으로 평가한다. 테스트 사용자, 생성 SQL, 비활성 모델, 결과는 모두 이 디렉터리에만 둔다.

## 측정 대상

- 데이터: MovieLens 25M 평점을 현재 DB의 TMDB ID로 매핑
- 분할: 사용자별 시간순 70% 학습, 이후 30% 평가 정답
- 학습 규모: `100 -> 150 -> 200 -> 300 -> 500명` 누적 코호트
- 고정 평가 표본: 최초 100명
- 사용자 유형: 학습 구간의 장르 엔트로피가 전체 적격 사용자 중앙값보다 낮은 `focused`
- 장르 분산: 학습 구간의 대표 선호 장르별로 순환 선발
- 최소 데이터: DB 매핑 평점 100개, 학습 양성 20개, 미래 양성 10개
- 학습 행동: `5.0=saved`, `3.5~4.5=pinned`, `0.5~1.5=passed`, `2.0~3.0=무시`
- NDCG 등급: 평점 `3.5/4.0/4.5/5.0`을 관련도 `1/2/3/4`로 변환
- 후보 수: 각 사용자의 평가 구간 30%에 포함된 리뷰 수와 동일
- 측정 위치: `NDCG@10`, `NDCG@20`처럼 10개 단위와 사용자별 전체 후보 수

예를 들어 리뷰가 100개인 사용자는 앞 70개로 학습하고 뒤 30개 영화를 후보로 삼아 `NDCG@10`, `NDCG@20`, `NDCG@30`을 측정한다. 리뷰가 1,000개라면 앞 700개로 학습하고 뒤 300개 영화를 후보로 삼아 10개 단위로 `NDCG@300`까지 측정한다.

후보군은 사용자별 뒤쪽 30%에 포함된 영화 자체다. V3 LightFM 하이브리드 모델이 이 영화들에 점수를 매겨 정렬하고, 같은 영화들을 실제 평점순으로 정렬한 정답과 NDCG를 비교한다. 이 평가는 전체 카탈로그에서 영화를 찾아내는 능력이 아니라 주어진 후보의 순위를 정하는 능력을 측정한다.

사용자 수에 따른 차이는 동일한 정답지로 비교한다. 500명을 한 번 선발한 뒤 작은 코호트가 큰 코호트에 포함되게 구성하고, 각 모델은 해당 규모의 앞쪽 70% 행동만 학습한다. 모든 모델의 NDCG는 동일한 최초 100명의 뒤쪽 30%로 계산한다. 따라서 평가 사용자 교체가 아니라 학습 사용자 증가에 따른 변화만 비교한다. 활동량으로 사용자를 나누지 않으며, 특정 장르 쏠림을 줄이기 위해 대표 장르 버킷을 순환한다.

모든 학습 행동 시각은 평가 기준일보다 31~365일 이전으로 옮겨 단기 취향이 개입하지 않게 한다.

## 디렉터리

```text
tests/v3_long_term_eval/
├── inputs/       # MovieLens CSV, Git 제외
├── generated/    # 계획, seed SQL, 비활성 모델, Git 제외
├── outputs/      # JSONL/JSON/Markdown 결과, Git 제외
├── run_pipeline.sh
├── run_pipeline.py
├── prepare.py
├── train_shadow_model.py
├── evaluate.py
├── metrics.py
├── movielens.py
└── 99_cleanup.sql
```

## 실행 순서

1. MovieLens 25M의 `ratings.csv`, `links.csv`, `movies.csv`를 `inputs/`에 둔다.

```bash
curl -LO https://files.grouplens.org/datasets/movielens/ml-25m.zip
unzip ml-25m.zip
cp ml-25m/ratings.csv ml-25m/links.csv ml-25m/movies.csv tests/v3_long_term_eval/inputs/
```

2. 이후 과정은 파이프라인을 한 번 실행한다.

```bash
bash tests/v3_long_term_eval/run_pipeline.sh
```

파이프라인은 터미널의 진행 표시 한 줄을 갱신하면서 다음 작업을 순서대로 수행한다.

- DB 카탈로그 매핑과 취향 집중형 누적 사용자 코호트 생성
- 이전 `v3eval-ml-*` 평가 사용자만 정리
- 최대 500명의 학습 구간 행동 seed를 한 번 삽입
- 100/150/200/300/500명별 비활성 shadow 모델 학습
- 각 모델을 동일한 최초 100명의 평가 구간으로 NDCG 계산
- 규모별 상세 결과와 비교표 저장

기본 온톨로지 build는 현재 검증된 `22`다. 사용자 규모를 바꿀 때만 오름차순 목록을 옵션으로 준다.

```bash
bash tests/v3_long_term_eval/run_pipeline.sh \
  --ontology-build-id 22 \
  --user-counts 100,150,200,300,500
```

평가가 끝난 사용자는 결과 분석을 위해 DB에 남는다. 실행 직후 자동 삭제하려면 다음 옵션을 사용한다.

```bash
bash tests/v3_long_term_eval/run_pipeline.sh --cleanup-after
```

중간 단계가 실패했지만 seed와 shadow 모델이 생성된 경우에는 앞 단계를 반복하지 않고 평가부터 재개한다.

```bash
bash tests/v3_long_term_eval/run_pipeline.sh --resume
```

생성한 shadow 모델은 serving bundle에 활성화되지 않는다. `evaluation_plan.jsonl`의 holdout도 DB에 삽입하지 않는다.

## 단계별 실행

파이프라인 문제를 진단할 때만 아래 명령을 개별 실행한다.

```bash
docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.prepare

docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < tests/v3_long_term_eval/generated/01_seed_movielens_users.sql

docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.train_shadow_model 22 \
  --plan tests/v3_long_term_eval/generated/cohorts/100/evaluation_plan.jsonl \
  --output-root tests/v3_long_term_eval/generated/models/100

docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.evaluate \
  tests/v3_long_term_eval/generated/models/100/<model-build-id> \
  --plan tests/v3_long_term_eval/generated/cohorts/100/evaluation_plan.jsonl \
  --output-dir tests/v3_long_term_eval/outputs/scales/100
```

평가 사용자를 나중에 정리할 때 첫 명령은 롤백되는 미리보기이고, 두 번째 명령만 실제 삭제한다.

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < tests/v3_long_term_eval/99_cleanup.sql

docker compose exec -T db sh -c 'psql -v commit_cleanup=true -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < tests/v3_long_term_eval/99_cleanup.sql
```

## 결과 해석

- `outputs/scales/<사용자 수>/`: 규모별 상세 결과
- `outputs/scales/<사용자 수>/long_term_evaluation_results.jsonl`: 사용자별 순위와 NDCG
- `outputs/scales/<사용자 수>/long_term_evaluation_summary.*`: 규모별 집계
- `outputs/scale_comparison.md`: 동일한 100명 기준의 규모별 NDCG 비교표
- 사용자별 NDCG는 후보 10개 단위와 해당 사용자의 전체 후보 수에서 기록한다.
- 전체 NDCG@K는 후보를 K개 이상 생성한 사용자만 평균에 포함한다.
- 이 평가는 주어진 미래 영화들의 평점 순위를 모델이 얼마나 잘 구분하는지 측정한다.
- 전체 카탈로그에서 정답 영화를 검색하는 능력은 이 결과로 판단하지 않는다.
- 합성·외부 평점 기반 결과이므로 실제 서비스 사용자 만족도를 확정하는 지표로 사용하지 않는다.
