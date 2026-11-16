# V3 장기 취향 NDCG 평가

이 디렉터리는 운영 코드와 아티팩트를 변경하지 않고 V3 장기 취향 추천을 오프라인으로 평가한다. 테스트 사용자, 생성 SQL, 비활성 모델, 결과는 모두 이 디렉터리에만 둔다. 완료된 일회성 튜닝의 집계와 판단은 루트의 `v3_recommendation_quality_tuning_results.md`에 보존하고, 전용 실행기와 원시 출력은 다음 실험에 필요하지 않으면 제거한다.

## 측정 대상

- 데이터: MovieLens 25M 평점을 현재 DB의 TMDB ID로 매핑
- 분할: 사용자별 시간순 70% 학습, 이후 30% 평가 정답
- 학습 규모: `100 -> 200 -> 300 -> 400 -> 500명` 누적 코호트
- 고정 평가 표본: 최초 100명
- 사용자 유형: 학습 구간 전반부와 후반부의 선호 장르 분포 차이가 적격 사용자 하위 25%인 `stable_low_variability`
- 장르 분산: 학습 구간의 대표 선호 장르별로 순환 선발
- 최소 데이터: DB 매핑 평점 100개, 학습 양성 20개, 미래 양성 10개
- 학습 행동: `5.0=saved`, `3.5~4.5=pinned`, `0.5~1.5=passed`, `2.0~3.0=학습 제외`
- 평가 정답 등급: `PASS=0`, `NEUTRAL=1`, `PIN=2`, `SAVED=3`
- 후보 수: 각 사용자의 평가 구간 30%에 포함된 리뷰 수와 동일
- 주 지표: 사용자별 holdout 영화 전체를 평가하는 `NDCG@100%`
- 보조 지표: `NDCG@20%`, K는 `ceil(holdout 영화 수 * 0.20)`, 최소 1개
- 고정 `NDCG@10/@20/@30`은 과거 결과 호환과 진단용으로만 유지

예를 들어 리뷰가 100개인 사용자는 앞 70개로 학습하고 뒤 30개 영화 전체의 순위를 `NDCG@100%`로 측정한다. 같은 결과의 상위 6개는 `NDCG@20%` 보조 지표로 확인한다. 리뷰가 1,000개라면 앞 700개로 학습하고 뒤 300개 전체가 주 평가 범위이며, 상위 60개가 보조 평가 범위다.

후보군은 사용자별 뒤쪽 30%에 포함된 영화 자체다. V3 LightFM 하이브리드 모델이 이 영화들에 점수를 매겨 정렬하고, 같은 영화들을 행동 등급순으로 정렬한 정답과 NDCG를 비교한다. 같은 행동으로 해석되는 `3.5~4.5`는 모두 `PIN=2`로 동등하게 취급한다. 이 평가는 전체 카탈로그에서 영화를 찾아내는 능력이 아니라 주어진 후보의 선호 등급 순서를 정하는 능력을 측정한다.

사용자 수에 따른 차이는 동일한 정답지로 비교한다. 500명을 한 번 선발한 뒤 작은 코호트가 큰 코호트에 포함되게 구성하고, 각 모델은 해당 규모의 앞쪽 70% 행동만 학습한다. 모든 모델의 NDCG는 동일한 최초 100명의 뒤쪽 30%로 계산한다. 따라서 평가 사용자 교체가 아니라 학습 사용자 증가에 따른 변화만 비교한다. 활동량으로 사용자를 나누지 않으며, 특정 장르 쏠림을 줄이기 위해 대표 장르 버킷을 순환한다.

모든 학습 행동 시각은 평가 기준일보다 31~365일 이전으로 옮겨 단기 취향이 개입하지 않게 한다.

## 디렉터리

```text
tests/v3_long_term_eval/
├── inputs/       # MovieLens CSV, Git 제외
├── generated/    # 현재 평가 계획과 실행 중 생성물, Git 제외
├── outputs/      # 현재 실험 결과, Git 제외
├── run_pipeline.sh
├── run_pipeline.py
├── prepare.py
├── prepare_matched_profiles.py
├── train_shadow_model.py
├── evaluate.py
├── evaluate_pure_lightfm.py
├── run_ablation_experiment.py
├── evaluation_dataset.py
├── metrics.py
├── movielens.py
└── 99_cleanup.sql
```

`generated/matched_profiles`, `generated/generalization_profiles`, `generated/identity_generalization_profiles`, `generated/factorial_replication_profiles`, `generated/final_generalization_profiles`의 평가 계획은 개발 사용자와 이미 소비된 일반화 사용자를 구분하기 위해 유지한다. 비활성 모델, 후보 snapshot과 seed SQL은 재생성 가능한 임시 산출물이다. `outputs/`에는 현재 판단 근거인 TUNE-21~23 원본만 이번 품질 반복이 끝날 때까지 유지하고 이후 요약 문서만 남긴다.

현재 장기 취향 core 결정은 루트 `v3_recommendation_quality_tuning_plan.md`의 TUNE-21~23에 기록돼 있다. TUNE-21과 TUNE-22에서 행동 입력, identity 표현과 LightFM 학습 설정을 통제 비교했고, TUNE-23에서 추가 설정 선택 없이 새 미사용 사용자로 최종 검증했다. 다섯 평가 집단의 사용자는 이후 튜닝에 재사용하지 않는다.

TUNE-21 통제 실험은 다음 명령으로 재현한다.

```bash
docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.run_lightfm_factorial
```

TUNE-21, 새 개발 집단 B의 재현 실험 TUNE-22와 최종 일반화 TUNE-23은 완료됐다. TUNE-22는 `generated/factorial_replication_profiles/evaluation_plan.jsonl`, TUNE-23은 `generated/final_generalization_profiles/evaluation_plan.jsonl`을 사용한다. TUNE-23은 순수 기준보다 NDCG@100% `+0.002747` 높아 사전 gate를 통과했다. 다음 품질 대상은 고정된 LightFM core와 ontology 점수의 결합 구조다.

TUNE-22는 다음 명령으로 재현한다.

```bash
docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.run_lightfm_factorial \
  --case-id TUNE-22 \
  --skip-anchor-check \
  --plan tests/v3_long_term_eval/generated/factorial_replication_profiles/evaluation_plan.jsonl
```

TUNE-23은 고정된 두 endpoint만 실행한다.

```bash
docker compose run --rm --no-deps back-api \
  python -m tests.v3_long_term_eval.run_lightfm_factorial \
  --case-id TUNE-23 \
  --comparison-only \
  --plan tests/v3_long_term_eval/generated/final_generalization_profiles/evaluation_plan.jsonl \
  --output-root tests/v3_long_term_eval/outputs/lightfm_core_generalization_ndcg100
```

고정 V3 LightFM core에 현재 최종 온톨로지 점수 25%를 결합한 영향은 개발 집단 A/B에서 다음 명령으로 진단한다. 빌드 22의 실제 그래프 엣지와 field-budget item feature를 사용하며 운영 모델과 bundle은 변경하지 않는다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_ontology_fusion_experiment
```

결과는 `outputs/ontology_fusion_ndcg100/TUNE-24/`에 저장된다. 이 실험은 각 사용자의 holdout 영화 내부에서 최종 온톨로지 점수 성분만 비교하며 전체 카탈로그 후보 검색과 다른 정책 재정렬은 포함하지 않는다.

TUNE-24에서 확인한 손실을 프로필 범위와 점수 정규화로 분리할 때는 같은 runner의 진단 grid를 사용한다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_ontology_fusion_experiment \
  --case-id TUNE-25 \
  --diagnostic-grid
```

`bounded_raw`는 field budget으로 이미 0~1에 제한된 온톨로지 원점수를 유지한다. `repeated_evidence`는 최소 두 positive 영화에서 반복된 특징만 유지한다. 두 변경은 동일한 LightFM 점수와 25% 온톨로지 비율에서 교차 비교된다.

TUNE-25의 bounded raw 기준에서 family별 기여를 확인할 때는 하나씩 제외하는 진단을 실행한다. 제외한 family의 budget은 다른 family에 재분배하지 않는다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_ontology_fusion_experiment \
  --case-id TUNE-26 \
  --family-ablation
```

결과는 `outputs/ontology_fusion_ndcg100/TUNE-26/`에 저장된다. 양의 제거 효과는 해당 family를 뺐을 때 순위가 개선됐다는 뜻이고, 음의 제거 효과는 해당 family를 뺐을 때 순위가 나빠졌다는 뜻이다.

70% 학습 구간의 장르 집중도와 bounded ontology 결합 손익의 관계는 다음 명령으로 진단한다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_ontology_fusion_experiment \
  --case-id TUNE-27 \
  --concentration-diagnostic
```

집중도는 학습 구간의 positive 영화 장르 엔트로피로 계산하며 holdout 평점은 사용하지 않는다. 현재 개발 집단은 낮고 높은 엔트로피 양 끝만 선발했으므로 TUNE-27은 방향 진단만 수행하고 운영 임계값을 선택하지 않는다.

TUNE-28은 이전 평가 사용자를 제외하고 안정 사용자의 엔트로피 전체 범위를 낮음·중간·높음으로 나눈 새 집단을 만든다. 각 구간의 사용자는 같은 대표 장르와 비슷한 리뷰 수를 가진 triplet으로 매칭된다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.prepare_continuous_entropy_profiles \
  --exclude-plan tests/v3_long_term_eval/generated/matched_profiles/evaluation_plan.jsonl \
  --exclude-plan tests/v3_long_term_eval/generated/generalization_profiles/evaluation_plan.jsonl \
  --exclude-plan tests/v3_long_term_eval/generated/identity_generalization_profiles/evaluation_plan.jsonl \
  --exclude-plan tests/v3_long_term_eval/generated/factorial_replication_profiles/evaluation_plan.jsonl \
  --exclude-plan tests/v3_long_term_eval/generated/final_generalization_profiles/evaluation_plan.jsonl
```

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_ontology_fusion_experiment \
  --case-id TUNE-28 \
  --concentration-diagnostic \
  --expected-users-per-cohort 300 \
  --development-a tests/v3_long_term_eval/generated/continuous_entropy_profiles/development_a/evaluation_plan.jsonl \
  --development-b tests/v3_long_term_eval/generated/continuous_entropy_profiles/development_b/evaluation_plan.jsonl
```

이 집단에서도 entropy는 결합 weight로 사용되지 않으며 결과를 구간별로 나누는 진단값으로만 사용한다.

TUNE-29는 TUNE-21~28 사용자를 모두 제외한 새 600명으로 순수 LightFM, 고정 V3 LightFM core와 bounded ontology를 결합한 V3를 한 번에 비교한다. 운영 모델과 bundle은 변경하지 않는다.

```bash
docker compose run --rm --no-deps back-seeder \
  python -m tests.v3_long_term_eval.run_final_generalization
```

결과는 `outputs/final_generalization_ndcg100/TUNE-29/`에 저장된다.

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

- DB 카탈로그 매핑과 취향 변동성이 낮은 누적 사용자 코호트 생성
- 이전 `v3eval-ml-*` 평가 사용자만 정리
- 최대 500명의 학습 구간 행동 seed를 한 번 삽입
- 100/200/300/400/500명별 비활성 shadow 모델 학습
- 각 모델을 동일한 최초 100명의 평가 구간으로 NDCG 계산
- 규모별 상세 결과와 비교표 저장

기본 온톨로지 build는 현재 검증된 `22`다. 사용자 규모를 바꿀 때만 오름차순 목록을 옵션으로 준다.

```bash
bash tests/v3_long_term_eval/run_pipeline.sh \
  --ontology-build-id 22 \
  --user-counts 100,200,300,400,500
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
- 사용자별 NDCG는 후보 10개 단위, 상위 20%, 해당 사용자의 전체 후보 수에서 기록한다.
- 전체 NDCG@K는 후보를 K개 이상 생성한 사용자만 평균에 포함한다.
- 모델 선택의 주 지표는 전체 후보를 사용한 `NDCG@100%`이고, 상위권 오류 확인에는 `NDCG@20%`를 함께 사용한다.
- 이 평가는 주어진 미래 영화들의 평점 순위를 모델이 얼마나 잘 구분하는지 측정한다.
- 전체 카탈로그에서 정답 영화를 검색하는 능력은 이 결과로 판단하지 않는다.
- 합성·외부 평점 기반 결과이므로 실제 서비스 사용자 만족도를 확정하는 지표로 사용하지 않는다.
