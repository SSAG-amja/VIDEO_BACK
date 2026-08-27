# V3 추천 품질 문제 분석 및 개선 계획

이 문서는 [추천 품질 기준점](11_recommendation_quality_baseline.md)에서 발견한 현상을 원인 단계별로 분해하고, 추천 품질 고도화 작업 순서를 정의한다. 서비스 세션, 페이지네이션, 배포 운영과 응답 시간 최적화는 이 문서의 범위가 아니다.

## 1. 먼저 내려야 할 결론

현재 결과만 보고 LightFM, ontology, 정책 가중치를 바로 조정하면 안 된다. 평가 시드와 모델 학습 상태에 먼저 해결해야 할 문제가 있어 변경 효과를 올바르게 해석할 수 없기 때문이다.

현재 확정할 수 있는 사실은 다음과 같다.

1. 최종 반환, 중복 제거, watched/passed 제외는 정상 동작한다.
2. LightFM parameter는 유한하지만 수치적으로 정상 범위를 크게 벗어났다.
3. 독립 단기 후보는 생성되지만 현재 병합식에서는 단기 전용 후보가 구조적으로 불리하다.
4. 취향 변화 신뢰도 공식은 변화가 없어도 충분한 최근 행동만으로 최대 0.5의 바닥값을 만든다.
5. 현재 drift 시드는 새 취향 행동을 모델 학습 전에 넣으므로 진짜 학습 후 취향 변화를 재현하지 않는다.
6. 현재 synthetic 협업 데이터는 영화와 취향 패턴이 지나치게 반복되어 협업 필터링 품질을 판단하기 어렵다.

## 2. 문제 상세

### P0. 현재 품질 평가 입력이 장기·단기 품질을 분리하지 못한다

#### P0-1. 취향군 영화 선정 조건이 너무 넓다

시드 영화는 취향군 장르 3개 중 하나라도 일치하면 통과한다. 그 결과 각 영화에서 목표 장르가 차지하는 평균 비율이 0.414~0.550에 불과하다. 애니메이션·가족·모험 집단에 모험 장르만 가진 실사 영화가 들어오는 식이다.

이 상태에서 장르 일치율이 낮으면 모델이 틀린 것인지 입력 영화가 애매한 것인지 분리할 수 없다.

#### P0-2. synthetic 협업 데이터가 지나치게 압축돼 있다

120명의 positive 행동은 약 3,333쌍이지만 서로 다른 영화는 213편뿐이다.

- 영화당 사용자 수 중앙값: 10명
- 95백분위: 35.4명
- 최대: 80명
- `데드풀`: 80명
- `아바타`: 64명
- `어벤져스`: 60명

사용자 수는 120명이지만 실질적인 취향 구조는 6개 큰 장르 집단과 소수의 인기 영화로 반복된다. 모델이 같은 영화를 여러 사용자에게 추천하는 현상에는 모델 문제뿐 아니라 이 입력 편향도 포함돼 있다.

#### P0-3. 단기 취향 변화가 모델 학습 데이터에 이미 포함된다

현재 `drift` 사용자의 최근 pinned/saved 행동은 반대 취향군 영화로 생성되지만, 이 행동을 넣은 뒤 LightFM을 학습한다. 따라서 게시된 장기 후보도 새 취향을 이미 학습한 결과다.

검증해야 하는 실제 흐름은 다음과 같아야 한다.

```text
과거 행동 생성
-> LightFM 학습 및 장기 후보 고정
-> 같은 취향의 최근 행동 또는 반대 취향의 최근 행동 추가
-> 단기 후보 갱신
-> 고정된 장기 후보와 단기 후보가 최종 결과에서 어떻게 결합되는지 비교
```

#### P0-4. 현재 간이 지표에 사각지대가 있다

현재 보고서는 장기/단기 장르 일치, 후보 출처, vote count, 정확한 영화 제외를 본다. 다음은 아직 측정하지 않는다.

- 단계별 단기 후보 생존 수: 100개 병합, hard filter 후, 최종 20개
- 최종 영화와 negative feature의 의미 중복
- 사용자 간 장기 후보 중복률과 상위 영화 집중도
- feature 종류별 ontology 기여도와 지나치게 일반적인 근거
- 추천 이유가 실제 상위 점수 변화와 일치하는지

### P1. LightFM 학습 결과가 수치적으로 발산했다

현재 hybrid 모델은 다음 크기다.

- 사용자: 120명
- positive interaction: 3,328개
- 전체 영화: 1,176,540편
- item feature: 1,502,427개
- item identity feature: 1,176,540개
- user feature matrix: `120 x 1,344`, `nnz=41,492`
- item feature matrix: `1,176,540 x 1,502,427`, `nnz=10,505,033`

장기 후보 원점수 절댓값 중앙값은 약 `7.07e11`, 최대는 약 `1.43e13`이다. artifact 내부에서도 다음 outlier가 확인됐다.

- user embedding norm 최대: 약 `3.68e6`
- item embedding norm 최대: 약 `1.83e6`
- item embedding 절댓값 최대: 약 `1.05e6`

현재 검증은 `NaN`과 무한대가 없는지만 검사한다. 따라서 유한하지만 사실상 발산한 모델이 정상 artifact로 게시될 수 있다. percentile 정규화는 큰 값을 0~1 순위값으로 바꿀 뿐, 잘못된 순위를 복구하지 않는다.

가능성이 높은 원인은 아직 가설로 관리한다.

1. 사용자 한 명당 평균 약 346개 feature를 합산하지만 feature family별 총량 정규화가 없다.
2. 영화 약 117만 편 모두에 identity parameter를 두지만 학습 positive 영화는 극히 일부다.
3. 3,328개 interaction에 비해 64 components, 40 epochs, learning rate 0.05, alpha `1e-6`은 과대 학습될 수 있다.
4. actor/keyword처럼 값이 많은 feature family가 genre/theme보다 표현 크기를 더 크게 만들 수 있다.

ontology edge 원천값은 actor/director/genre/keyword가 1.0이고 theme/mood도 최대 1.0 미만이므로, DB edge strength 자체가 `1e13` 점수를 직접 만든 것은 아니다.

### P2. 현재 LightFM 장기 후보는 개인화와 협업 효과를 구분하기 어렵다

장기 후보 480칸에서 서로 다른 영화는 79편뿐이다. 하지만 이것을 곧바로 “협업 필터링 실패”라고 단정할 수는 없다. 120명이 213편을 집중 소비하도록 만든 시드가 공통 순위를 학습시키기 때문이다.

또한 현재 user feature는 명시적 선호 장르와 favorite 영화 파생 feature로 구성된다. saved/pinned/watched는 interaction matrix에는 들어가지만 user semantic feature에는 직접 들어가지 않는다. 다음 세 접근을 분리 비교할 필요가 있다.

1. 사용자 identity + item ontology feature만으로 행동에서 관계를 학습
2. onboarding/favorite user feature를 함께 사용한 현재 방식
3. 정규화된 장기 행동 semantic profile까지 user feature에 포함

3번은 무조건 더 좋은 방식이 아니다. 행동을 interaction과 user feature에 중복 반영할 수 있으므로 ablation으로 판단해야 한다.

### P3. 취향 변화 신뢰도 공식이 안정 사용자를 변화 사용자로 오인한다

현재 공식은 개념적으로 다음과 같다.

```text
activity = min(최근 positive 행동 수 / 5, 1)
novelty = feature 종류별 최근 값의 신규 비율 평균
consistency = 최근 positive / 최근 전체 행동
drift = activity * (0.5 + 0.5 * novelty) * consistency
```

문제는 세 가지다.

1. `novelty=0`이어도 행동이 5개 이상이고 모두 positive이면 `drift=0.5`다.
2. 장르, 테마, 분위기뿐 아니라 배우, 감독, 키워드도 동일한 한 표로 평균한다.
3. 과거 행동이 부족해 비교할 feature가 없어도 activity만으로 변화처럼 보일 수 있다.

같은 장르의 다른 영화를 보면 배우와 감독은 자연히 달라진다. 따라서 `stable` 평균 0.528과 실제 `drift` 평균 0.622가 충분히 분리되지 않는다.

취향 변화는 하나의 숫자에 바로 섞기 전에 다음 두 값으로 분리해야 한다.

- **최근 취향 근거량**: 최근 행동 수, 행동 강도, 시간 감쇠
- **과거 대비 의미 거리**: 최근 취향과 과거 장기 취향이 실제로 얼마나 다른가

과거 이력이 없는 사용자는 “취향 변화”가 아니라 “최근 취향만 확인 가능”한 상태로 다뤄야 한다.

### P4. 현재 병합식에서는 단기 전용 후보가 장기 후보를 이기기 어렵다

현재 후보 선택 점수는 다음과 같다.

```text
(1 - drift_weight) * normalized_long
+ drift_weight * normalized_short

drift_weight = drift_confidence * 0.45
```

예를 들어 `drift_confidence=0.62`이면 단기 비중은 약 0.279다.

- 장기에만 있는 후보의 가능한 최대 점수: 약 0.721
- 단기에만 있는 후보의 가능한 최대 점수: 약 0.279

심지어 `drift_confidence=1.0`이어도 장기 전용 최대는 0.55, 단기 전용 최대는 0.45다. 따라서 단기 후보가 장기 후보와 겹칠 때는 살아남지만, LightFM이 전혀 찾지 못한 새로운 단기 취향 영화는 구조적으로 불리하다.

현재 contextual floor는 병합 후보 100개 안에 단기 후보를 일부 넣어줄 뿐이다. 최종 정책 상위 20개에서 단기 후보를 보존하지 않으므로 실제 결과에서는 480칸 중 18칸만 단기 출처로 남았다.

단순히 `0.45`를 더 큰 숫자로 바꾸는 것은 적절하지 않다. 잘못 측정된 drift가 그대로 최종 목록을 뒤집을 수 있기 때문이다. P3 보정 후 장기와 단기를 별도 lane으로 선정하는 방식이 더 명확하다.

### P5. ontology 점수는 넓은 의미 일치를 과대평가할 가능성이 있다

현재 ontology 점수는 장르, 키워드, 배우, 감독, 테마, 분위기의 family별 damped score를 더한 뒤 후보 집합 안에서 percentile 정규화한다.

- feature family별 명시적인 positive 가중치가 없다.
- catalog 전체에서 흔한 feature와 희귀한 feature의 정보량 차이를 반영하지 않는다.
- 장르가 많은 영화 또는 흔한 theme/mood를 가진 영화가 여러 약한 일치를 쌓을 수 있다.
- 후보 집합이 바뀌면 같은 raw score도 percentile이 달라진다.

이는 현재 단계에서는 가설이다. 먼저 LightFM과 단기 병합을 안정화한 뒤 ontology weight 0인 결과와 비교해 실제 영향도를 확인해야 한다.

### P6. 저신뢰 catalog 영화는 감점되지 않고 보너스만 받지 않는다

현재 품질 점수는 vote count가 많고 평점이 높으면 최대 0.08을 더한다. `vote_count=0`인 영화는 보너스가 0일 뿐 감점이나 제외가 없다. 모델 또는 ontology 기본 점수가 높으면 그대로 상위에 남을 수 있다.

최종 480칸 중 105칸이 `vote_count<20`, 37칸이 `vote_count=0`이었다. long-tail을 모두 제거할 필요는 없지만, 증거가 없는 영화와 적게 알려진 좋은 영화를 구분하는 신뢰도 단계가 필요하다.

### P7. negative 취향 품질은 아직 검증하지 않았다

정확히 passed한 영화의 재노출은 0건이지만, passed 영화와 같은 장르·테마·배우를 가진 영화가 얼마나 줄었는지는 측정하지 않았다. `negative_heavy` 결과가 정상이라는 결론은 아직 내릴 수 없다.

현재 negative penalty는 evidence가 충분해도 최대 0.20이며 base score의 30%를 넘지 않는다. 이 수치가 적절한지는 negative semantic overlap을 추가한 뒤 판단해야 한다.

## 3. 개선 실행 계획

### Phase A. 품질 평가 기준부터 교정

현재 144명 seed는 파이프라인과 응답 계약 회귀용으로 유지한다. 추천 품질용 시나리오는 별도 단계로 구성한다.

1. 학습 전 과거 행동만 가진 known user를 만든다.
2. cohort 영화는 목표 장르 비율, 최소 vote count, overview/theme 근거를 조합해 의미 순도를 높인다.
3. 큰 6개 집단 안에서 세부 취향을 나눠 사용자별 행동 영화가 지나치게 반복되지 않게 한다.
4. 모델과 장기 후보를 고정한 뒤 stable control에는 같은 취향 행동, drift user에는 다른 취향 행동을 추가한다.
5. 단기 후보를 갱신하고 동일 사용자의 장기 후보, 단기 후보, 최종 결과를 비교한다.
6. 단계별 source 생존 수와 negative 의미 중복을 보고서에 추가한다.

완료 조건:

- stable과 drift의 입력 의미 거리가 명확히 다르다.
- drift의 최근 행동은 model cutoff 이후에만 존재한다.
- 사용자별 행동 영화와 cohort별 영화 중복 통계가 보고서에 기록된다.
- 같은 입력으로 반복 실행했을 때 동일 결과를 낸다.

### Phase B. LightFM 수치 안정화와 장기 후보 복구

먼저 artifact 게시 gate를 추가한다.

- embedding/bias 절댓값 및 norm의 median, p95, p99, max
- 표본 prediction 분포
- 사용자 간 top-K 중복률과 고유 영화 수
- interaction 수 대비 학습 parameter 및 feature 수

그다음 아래 구조를 같은 데이터로 ablation한다.

1. 현재 full identity + ontology feature
2. identity와 semantic block의 총량을 각각 제한한 row/family 정규화
3. interaction 근거가 있는 영화만 identity를 유지하고 나머지는 ontology feature-only로 추론
4. item identity를 제거한 metadata-only 진단 모델

구조를 결정한 뒤에만 components `16/32`, learning rate `0.01` 부근, epochs `10/20`, alpha 상향을 좁은 범위에서 비교한다. 숫자 조합 전체를 무작위 탐색하지 않는다.

완료 조건:

- 현재와 같은 `1e13` prediction과 `1e6` embedding outlier가 없다.
- stable 사용자 사이에서도 세부 취향에 따라 장기 top-K가 달라진다.
- 모델 후보를 사람이 확인했을 때 cohort 방향을 벗어난 명백한 사례가 기준점보다 줄어든다.
- 활성 bundle은 교체하지 않고 별도 artifact 결과를 먼저 비교한다.

### Phase C. 취향 변화 판정 재설계

1. 최근 근거량과 과거 대비 의미 거리를 분리 계산한다.
2. genre/theme/mood를 주 비교축으로 두고 keyword/actor/director는 보조축으로 검증한다.
3. 과거 근거가 부족한 경우 drift가 아니라 recent-interest 상태로 분류한다.
4. 안정, 혼합, 실제 변화 시나리오의 값 분포를 본 뒤 threshold를 정한다.

완료 조건:

- stable 신뢰도 분포와 drift 신뢰도 분포가 겹치는 구간이 현재보다 명확히 줄어든다.
- 의미 변화가 없는 최근 positive 행동만으로 drift가 0.5가 되지 않는다.
- 행동 수만 많은 사용자가 자동으로 변화 사용자로 분류되지 않는다.

### Phase D. 단기 후보 병합과 최종 생존 보정

가중합 하나로 모든 후보를 경쟁시키지 않고 장기 lane과 단기 lane을 분리한다.

1. drift 구간별로 병합 100개에 들어갈 단기 전용 후보 수를 정한다.
2. 중복 영화는 model+short 근거를 모두 유지한다.
3. 최종 20개에서도 검증된 drift 수준에 따라 최소 단기 lane을 보존한다.
4. 나머지 슬롯은 전체 점수로 경쟁시킨다.
5. 각 단계에서 단기 후보의 탈락 이유를 기록한다.

정확한 비율은 Phase C 결과를 보고 정한다. 초기 비교안은 stable `0~10%`, 중간 변화 `15~25%`, 높은 변화 `30~40%` 범위이며 확정 정책값이 아니다.

완료 조건:

- post-model drift 사용자의 최종 목록에 단기 전용 후보가 실제로 나타난다.
- stable 사용자의 장기 추천이 불필요하게 흔들리지 않는다.
- 단기 후보가 포함된 이유와 탈락한 이유를 source trace로 확인할 수 있다.

### Phase E. ontology 기여도 보정

1. ontology component를 0으로 둔 결과와 현재 0.25 결과를 비교한다.
2. feature family별 기여 분포를 기록한다.
3. catalog frequency를 이용한 specificity 또는 family별 예산을 검토한다.
4. overview에서 파생한 theme/mood가 장르 하나만 일치하는 영화를 실제로 구분하는지 표본 확인한다.

완료 조건:

- ontology를 더했을 때 장기/단기 의미 일치가 개선되는지 설명할 수 있다.
- 흔한 feature 여러 개를 가진 영화가 단순 합산으로 상위에 오르지 않는다.
- LightFM 점수와 ontology 근거를 계속 분리 기록한다.

### Phase F. catalog 신뢰도와 negative 정책 보정

1. `vote_count=0`, `1~19`, `20 이상`을 분리해 노출 위치를 비교한다.
2. hard filter보다 신뢰도 감점과 제한된 슬롯을 먼저 비교한다.
3. negative profile과 최종 결과의 feature overlap을 추가한다.
4. exact passed 제외와 semantic negative 감점을 별도로 검증한다.

완료 조건:

- 근거가 거의 없는 영화가 상위 결과를 점유하지 않는다.
- long-tail 후보를 일괄 제거하지 않는다.
- negative-heavy 사용자에게 passed 취향과 의미상 유사한 영화가 과도하게 남지 않는다.

## 4. 권장 결정

현재 기준에서 다음 방향을 권장한다.

1. 기존 seed는 동작 회귀용으로 보존하고 품질 시드를 분리한다.
2. 첫 구현은 LightFM 가중치 조정이 아니라 품질 시나리오 분리와 수치 gate 추가다.
3. 단기 반영은 단순 가중치 상향보다 drift 기반 장기/단기 lane 방식으로 간다.
4. 저투표 영화는 당장 전부 hard filter하지 않고 신뢰도 감점부터 비교한다.
5. ontology 점수 조정은 LightFM과 drift/merge 문제를 고친 뒤 진행한다.

전체 순서는 `평가 교정 -> LightFM 안정화 -> drift 판정 -> 단기 병합 -> ontology -> catalog/negative`다.

## 5. Phase A 실행 결과

Phase A의 post-model known-user 시나리오를 `2026-08-27`에 실제 DB, Redis, production short-term worker와 V3 serving path로 실행했다.

- stable control: 기존 장기 취향과 같은 cohort의 최근 saved 영화 6개
- post-model drift: 기존 장기 취향의 반대 cohort에 해당하는 최근 saved 영화 6개
- 대상: 학습 당시 stable이었던 known user 12명
- 장기 모델 및 candidate snapshot: 변경하지 않음
- 시드 영화 조건: 목표 장르 비율 50% 이상, vote count 100 이상
- 원본 결과: [post-model quality snapshot](diagnostics/v3_quality_snapshot_20260827T142248Z.md)

| 항목 | post-model stable | post-model drift |
| --- | ---: | ---: |
| drift confidence | 0.701 | 0.737 |
| novelty | 0.564 | 0.737 |
| 단기 후보와 단기 장르 | 0.857 | 0.848 |
| 최종 결과와 단기 장르 | 0.338 | 0.165 |
| 병합 150개의 단기 source | 41.3 | 42.8 |
| eligibility 100개의 단기 source | 11.8 | 14.0 |
| 최종 20개의 단기 source | 0.8 | 0.0 |

이 결과로 다음 사항을 확정했다.

1. 독립 단기 후보 생성기는 새 취향 방향의 영화를 찾는다.
2. stable과 drift의 novelty에는 차이가 있지만 현재 confidence 공식이 이를 `0.701`과 `0.737`로 압축해 사실상 구분하지 못한다.
3. drift 사용자의 단기 후보는 병합과 eligibility까지 남지만 최종 정책 상위 20개에서 전부 탈락한다.
4. 따라서 단기 품질 문제의 주 원인은 후보 생성 실패가 아니라 P3 취향 변화 판정과 P4 병합·최종 생존 구조다.
5. 장기 raw score는 이 표본에서도 중앙값 약 `1.18e12`, 최대 약 `7.06e12`로 P1 수치 발산이 재현됐다.

Phase A 구현물은 다음과 같다.

- `tests/v3_user_seed/03_seed_post_model_quality_actions.sql`
- `tests/v3_user_seed/04_cleanup_post_model_quality_actions.sql`
- `tests/v3_user_seed/sync_redis.py --quality-post-model`
- `app/jobs/recsys/v3/diagnostics/quality_snapshot.py --scenario post-model`
- candidate merge의 model-only, short-only, overlap 진단

전체 V3 단위 테스트 93개가 통과했다. 추천 점수와 순위 정책은 Phase A에서 변경하지 않았다.

다음 작업은 Phase B다. 먼저 현재 artifact가 다시 정상으로 게시되지 않도록 수치 health gate를 추가하고, active bundle과 분리된 실험 artifact에서 feature 정규화와 item identity 범위를 비교한다.
