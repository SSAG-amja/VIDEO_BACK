# 01. V3 설계 및 구현 순서

## 1. 목적

이 문서는 V3 구현의 순서, 단계 간 의존성, 현재 진행 상태만 관리한다.

세부 구현은 [02 구현 방식](02_implementation_guide.md), 서비스 동작은 [03 추천 정책](03_recommendation_policy.md), 조정값은 [04 LightFM 조정 지점](04_lightfm_tuning.md), 그래프는 [05 온톨로지 구조](05_ontology_structure.md)를 따른다.

## 2. 최종 구조

```text
LightFM
  사용자 행동과 identity/ontology feature를 학습
  -> 장기 취향 추천 후보와 model score 생성

Ontology
  사용자 취향과 영화의 의미 관계를 표현
  -> item/user feature, 후보 evidence, 단기 취향 후보 생성

Policy engine
  hard filter, OTT, 부정 취향, 품질, 반복 감점 적용
  -> 최종 순위와 정책 효과 기록
```

계층 간 출력은 다음처럼 분리한다.

```text
model_score
ontology_score / ontology_evidence
policy_adjustments
final_score
recommendation_reasons
```

온톨로지 evidence는 LightFM 점수의 인과적 설명이 아니다.

## 3. 구현 의존 순서

```text
S0 엔진 경계와 dependency
 |
 v
S1 학습 데이터 계약
 |
 v
S2 온톨로지 schema 및 feature exporter
 |
 +-------------------+
 v                   v
S3 LightFM baseline  S4 runtime profile 기반
 |                   ontology 분석 준비
 +---------+---------+
           v
S5 Hybrid LightFM 및 artifact
           |
           v
S6 top-150 저장 materialization
           |
           v
S7 ontology analyzer + 단기 후보
           |
           v
S8 policy engine + cold-start
           |
           v
S9 V3 serving 연결
```

## 4. 단계별 작업

### S0. 엔진 경계와 dependency

상태: 완료

- HTTP API V1과 추천 엔진 V1/V2/V3를 별도 버전 축으로 분리
- engine registry를 `app/services/recsys`에 배치
- `python:3.11` builder와 `python:3.11-slim` runtime 구성
- LightFM fit/predict/feature-only inference/joblib reload 확인
- V3 adapter는 미완성 상태에서 명시적으로 실패

### S1. 학습 데이터 계약

상태: 현재 범위의 데이터·feature·profile 계약 구현 완료

구현됨:

- saved, pinned, watched, onboarding favorite를 WARP positive로 추출
- passed를 positive에서 제외하고 serving exclusion으로 보존
- 동일 사용자-영화 행동 중복을 strongest action + bounded bonus로 집계
- V1 최근성 bucket을 sample weight에 적용
- non-adult catalog와 model user mapping 생성
- interaction/sample-weight COO matrix와 dataset hash 생성
- 영화·플레이리스트 게시글 작성, 영화 게시글 좋아요, 댓글의 raw signal 추출
- 게시·댓글 시점 playlist 구성에 대한 `1/N` 투영과 source provenance
- social raw signal을 LightFM positive와 분리한 diagnostics/hash
- timestamp 없는 영화 게시글 좋아요의 현재 시점 build 제한
- identity/genre/keyword/actor/director/theme/mood/OTT feature source registry
- consumer별 `required/optional/disabled`와 source readiness 검증
- onboarding, long-term, short-term profile 및 OTT serving context schema
- feature retained/dropped coverage diagnostics 계약

남음:

- social raw signal의 방향 판정과 training eligibility 정책
- eligible social signal의 weight, log saturation, social cap 구현
- playlist post like를 위한 `likes.created_at` 또는 action-time 구성 이력
- 현재 snapshot table 한계를 반영한 시간 분할 경계
- 실제 DB snapshot 규모 진단

관련 코드:

- `app/jobs/recsys/v3/datasets/dataset_schemas.py`
- `app/jobs/recsys/v3/datasets/dataset_builder.py`
- `app/services/recsys/v3/domain/feature_registry.py`
- `app/services/recsys/v3/domain/schemas.py`
- `app/services/recsys/v3/policy/policy_registry.py`

### S2. 온톨로지 schema 및 feature exporter

상태: 완료. full graph build와 full-catalog item feature export 실측 완료

구현됨:

- V2/V3 ontology build 활성 상태를 engine/schema version별로 분리
- `person` node와 actor/director relation 확정
- OTT streaming/rent/buy relation 분리
- canonical semantic edge와 evidence row 분리
- actor 및 overview theme/mood를 V3 정상 build validation의 필수 경로로 지정
- source family 내부 max와 family 간 bounded union semantic 집계
- immutable build, source fingerprint 시작/종료 검증, stage metrics 구현
- 공유 UNLOGGED catalog와 실제 movie ID 범위를 이용한 factual edge batch
- 1,000편 단위 공유 큐에서 다음 청크를 가져오는 4-worker 동적 병렬 build
- worker별 chunk/row/elapsed metric과 실패 시 staging cleanup
- V3 전용 ontology asset `0.2.0`과 전체 concept derivation 검증
- 30 theme·16 mood를 유지하면서 명확한 DB keyword 중심으로 relation 114개 보강
- 중복 ontology index 제거와 build stage별 `ANALYZE`/work memory 적용
- feature export 검증 전 V3 build 활성화 금지
- movie identity와 genre/keyword/actor/director/theme/mood의 deterministic CSR item feature export
- actor/director/keyword movie frequency pruning과 keyword catalog ratio 상한
- semantic `effective_strength` feature value, actor/director role namespace 분리, OTT 제외
- feature별 source/retained/dropped/coverage/nnz 진단
- ontology build ID/schema/source hash와 movie/feature mapping hash를 고정한 manifest

남음:

- 영화 updater 변경분 기록과 graph 영향 분류
- startup/updater 완료 시 source 변경을 감지해 overview 추출과 새 graph build를 자동 예약하는 orchestration
- advisory lock 기반 중복 build 방지와 자동 갱신 실행 이력 기록

V3 asset 보강은 새 concept을 늘리지 않았다. 100편 rollback 비교에서 기존 asset 대비 evidence는 약 27%, canonical semantic edge는 약 23% 증가했다. 전체 DB 추정 evidence는 약 175만 개에서 208만 개로 약 19% 증가하므로 설명 coverage를 넓히되 graph 규모가 급증하지 않는 범위로 제한했다.

Full graph build `22`는 498.3초에 node 3,756,594개, edge 12,640,874개, evidence 2,078,395개를 생성했다. Full-catalog item feature export는 77.8초에 `1,176,540 x 1,502,427`, `nnz=10,505,033` CSR을 생성했고 CSR 88,746,428 bytes, peak RSS 918,224,896 bytes를 기록했다. 상세 값은 `diagnostics/item_feature_export_build_22.json`에 고정한다.

상세 구조는 [05 온톨로지 구조](05_ontology_structure.md)를 따른다.

### S3. Identity-only LightFM baseline

상태: 구현 완료, 실제 데이터 model build 대기

- S1 interaction과 sample weight로 WARP 모델 학습
- user/movie identity feature 유지
- model config, mapping, dataset hash, random seed 저장
- model artifact save/load 경계 구현

온톨로지 feature 적용 전에 identity-only 모델을 만들어 협업 신호 기준선을 고정한다.

구현 위치:

```text
app/jobs/recsys/v3/training/trainer.py
app/jobs/recsys/v3/training/model_schemas.py
app/jobs/recsys/v3/training/artifact_publisher.py
app/jobs/recsys/v3/training/train_identity_model.py
```

WARP 기본 설정, 행동 데이터 정책 snapshot/hash, user/movie mapping, dataset hash, package version을 불변 artifact에 저장한다. 임시 디렉터리에서 모든 파일 hash와 재로딩 prediction hash를 검증한 뒤에만 원자적으로 게시한다. identity-only artifact는 ontology build 의존성이 없음을 명시하며 serving bundle을 활성화하지 않는다.

2026-08-20 실제 DB 확인에서 학습 catalog와 graph build `22`의 movie node가 모두 `1,176,540`편으로 일치했다. 이후 seed 학습 사용자 120명을 삽입해 S5 hybrid baseline을 만들었으며, S3 identity-only 실제 artifact와 정확도 비교는 현재 범위에서 제외한다. trainer는 user 또는 positive pair가 없는 snapshot을 계속 거부하고 빈 model artifact를 만들지 않는다.

### S4. Runtime profile 기반 준비

상태: 구현 완료, seed online 검증 완료, production 사용자 분포 검증 대기

- long-term positive/negative ontology profile
- 최근 명시 행동 기반 short-term profile
- action, source movie, timestamp, decay, evidence provenance 기록
- actor/director/theme/mood feature cap과 top-K 진단

S2 item feature exporter와 feature namespace를 공유하지만 학습 feature와 runtime evidence를 같은 값으로 간주하지 않는다.

구현 위치는 `app/services/recsys/v3/profiles/profile_builder.py`와 `schemas.py`다. 명시적으로 선택한 성공한 V3 ontology build에서 사용자 행동 영화의 edge만 한 번의 set-based query로 읽는다. positive/negative feature마다 행동, 시각, recency, graph edge ID, edge strength, 영화 내 관계군 정규화, 기여 점수를 보존한다.

현재 범위:

```text
long-term positive: favorite, saved, pinned, watched
long-term negative: passed
serving exclusion: watched, passed
short-term: 최근 30일 명시 행동 최대 50개
short-term positive: saved, pinned, watched
short-term negative: passed
```

passed와 positive 상태가 같은 영화에 남아 있으면 dataset builder와 동일하게 passed를 우선한다. social raw signal은 방향성이 확정되지 않았으므로 runtime profile에도 아직 합산하지 않는다. actor처럼 한 영화에 값이 많은 관계군은 `1/sqrt(family_size)`로 영화별 기여를 정규화한 뒤 feature별 score cap과 top-K를 적용한다. retained feature당 provenance는 기여도가 큰 8개 edge로 제한하고 전체 기여 수를 별도로 기록한다.

단기 drift는 최근 positive 행동량, 장기 대비 관계군별 novelty, positive 일관성을 분리 기록한다. actor cardinality가 genre보다 drift를 지배하지 않도록 feature 개수가 아니라 관계군별 novelty를 같은 비중으로 평균한다. passed만으로 positive contextual profile이나 drift를 만들지 않는다.

Build `22` 표본 영화에서 profile 관계 56개를 약 `0.003`초에 조회했고 6개 관계군이 모두 반환됐다. Seed 144명의 profile 분포와 online 실행시간을 검증했으며 production 사용자 분포 검증은 별도로 남아 있다.

### S5. Hybrid LightFM 및 artifact

상태: 구현 완료, 실제 행동 데이터 hybrid model build 대기

- identity + ontology item feature 모델
- 선택된 user feature 연결
- model build와 ontology build compatibility 검증
- model, mapping, feature registry, manifest를 하나의 immutable artifact로 저장
- 검증된 serving bundle만 활성화

구현 위치:

```text
app/jobs/recsys/v3/features/user_feature_builder.py
app/jobs/recsys/v3/training/trainer.py
app/jobs/recsys/v3/training/artifact_publisher.py
app/jobs/recsys/v3/training/train_hybrid_model.py
```

item 입력은 S2의 `movie identity + genre/keyword/actor/director/theme/mood` 전체 CSR이다. user 입력은 현재 학습 사용자 identity, 전체 retained genre vocabulary, 현재 사용자 onboarding favorite 영화에서 실제 관측된 retained ontology token만 사용한다. explicit genre는 `1.0`, favorite-derived feature는 item edge 값을 `0.5`배 한 뒤 같은 user-token에서 최대값을 사용한다. 행동 기반 동적 취향은 interaction 행렬과 S4 runtime profile이 담당하므로 user feature에 다시 넣지 않으며 OTT도 포함하지 않는다.

학습 전 dataset/user/item 순서를 exact 비교하고 ontology build/status/schema/source, parent item export hash를 검증한다. artifact는 model, sparse user/item matrix, entity/feature mapping, 각 exporter manifest와 feature registry version을 저장한다. 모든 파일 hash, mapping hash, model embedding 차원, 저장 전후 prediction hash가 일치해야 원자적으로 게시한다. 합성 matrix에서 신규 사용자와 신규 영화가 identity 없이 공유 feature만으로 finite score를 받는 경로를 확인했다.

실제 build `22`는 `success`이며 기존 full export는 movie `1,176,540`, feature `1,502,427`, `nnz=10,505,033`이다. Seed 사용자 기반 S5 hybrid model은 생성·재로드 검증을 완료했다. S3 identity-only 실제 build와 S3/S5 정확도 비교는 현재 검증 범위에서 제외한다.

### S6. 사용자별 top-150 저장과 top-100 상세 처리

상태: 구현 완료, seed 120명 top-150 실행 및 게시 완료

- `32 users x 8,192 items` 기본 block에서 LightFM representation을 계산하고 사용자별 exact top-K만 유지한다. 전체 dense user-movie score matrix는 생성하지 않는다.
- watched/passed 영화는 top-K 선택 전에 제외하며, 동점은 movie ID 오름차순으로 결정한다.
- 1,024명마다 재시작 가능한 checkpoint shard를 기록하고 사용자 block 실패 시 1명 단위로 재시도해 실패 사용자를 격리한다.
- 모든 shard의 hash, rank, 중복, finite score를 검증한 뒤 불변 candidate snapshot으로 원자 게시한다.
- DB 게시 직전에 eligible 사용자와 watched/passed exclusion hash를 다시 검증한다. 게시 함수는 내부 commit을 하지 않으며 성공 사용자만 한 트랜잭션에서 교체하고 실패 사용자의 이전 추천은 유지한다.
- model raw score, model source rank, model build ID, candidate snapshot ID를 분리 저장한다.

합성 hybrid artifact에서 full LightFM prediction과 blockwise 결과 일치, watched/passed 제외, 결정적 동점, 실패 격리, snapshot 재로딩과 게시 payload를 확인했다. 초기 top-100 기준선은 120명 12,000개였고, P0-01 이후 top-150 snapshot `cand-950d86d7f1f978f316f2b773`에 18,000개를 게시했다. 예비 후보는 hard filter 보충에만 쓰며 상세 분석은 최대 100개다.

### S7. Ontology analyzer와 단기 후보

상태: 구현 완료, seed 실제 사용자 분포 및 Redis 선계산 검증 완료

- `short_term_retriever.py`는 최근 positive profile feature를 graph target index에서 역조회해 LightFM과 독립적인 `short_term_context` 후보를 생성한다. watched/passed는 조회 전에 제외한다.
- source raw score는 source 전체 집합에서 percentile 정규화하며 후보를 top-100으로 자른 뒤 다시 fit하지 않는다.
- `drift_confidence * 0.45`를 초기 drift weight로 사용하고 강한 drift에서는 short-term contextual floor를 적용한다. 값은 provisional config로 노출한다.
- `candidate_merger.py`는 model/short-term raw score, normalized score, source rank를 분리한 채 순위 후보를 최대 150개로 만든다.
- `ontology_analyzer.py`는 최대 100개 ID와 bounded profile feature 배열만 set-based query로 전달한다. 장기/단기, positive/negative, genre/keyword/actor/director/theme/mood 숫자 집계를 분리한다.
- OTT evidence는 graph 학습 feature가 아니라 최신 `movie_otts.is_streaming` 조회로 분리한다.
- 후보별 N+1, 요청별 full graph scan, 전체 후보 detailed evidence path 선계산은 하지 않는다.
- `retrieval_pipeline.py`가 short-term retrieval, source merge top-150, 저비용 hard filter, 최대 100개 bounded analyzer 순서를 고정한다.
- 최근 24시간 positive를 영화별 최대 가중치로 누적한다. distinct 영화 3개 또는 distinct 2개이면서 합계 2.0 이상일 때만 독립 단기 후보 갱신이 성립한다.
- 기준 성립 후 30초 debounce, 최초 성립 후 최대 2분을 적용한다. positive 삭제는 debounce 후 강제 갱신하고 passed/OTT 변경은 후보 재생성 없이 즉시 filter/policy에만 반영한다.
- `short_term_candidate_worker.py`가 due user를 processing lease로 원자 선점한다. 계산 중 revision이 바뀌면 다시 예약하고, worker가 종료되면 15분 뒤 lease가 만료돼 자동 재시도한다.
- 단기 후보 top-100은 `ontology_build_id + user_id + cache format` 서명으로 Redis에 materialize한다. 저장 시점부터 6시간에 사용자별 결정적 0~30분 jitter를 더해 만료를 분산한다. profile version과 벽시계 bucket은 서명에서 제외해 기준 미달 행동이나 시각 경계가 reverse lookup을 유발하지 않게 한다.
- 요청은 cache hit를 우선 사용하고 최신 watched/passed를 cached 후보에서 제거한다. Redis 장애, cache miss, 손상 payload, ontology 불일치에서는 DB graph retrieval로 fallback한다. profile, LightFM, ontology analyzer, OTT, hard filter와 policy reranking은 요청마다 최신 상태로 수행한다.

합성 검증에서 source 정규화, drift 0 장기 후보 유지, 강한 drift contextual floor, source score 분리, ontology scope/direction/type 및 OTT 분리를 확인했다. Build `22` read-only 검증에서 가장 넓은 장르 `18` reverse edge `287,674개` 기준 후보 20개 exact retrieval 약 `0.95`초, analyzer 약 `0.11`초, 전체 약 `1.06`초였다. 실제 p95가 허용 기준을 넘으면 의미 없는 임의 cap 대신 feature별 사전 top-N inverted retrieval artifact를 별도 설계한다.

`ontology_cold_item`, hard filter, policy adjustment, detailed explanation path는 S7에 포함하지 않았으며 다음 단계 책임으로 유지한다.

### S8. Policy engine과 cold-start

상태: 구현 완료, seed 실제 사용자·model artifact 평가 완료

- watched/passed/adult/title/session/명시 차단/취소 상태/subscribed OTT hard filter
- personal·ontology source 점수와 policy adjustment를 분리한 candidate trace
- vote count 신뢰도를 먼저 적용한 bounded 품질과 bounded negative/OTT/recency 조정
- genre/actor/director/theme/mood feature 집합의 반복 감점과 결정적 MMR
- onboarding ontology rule과 LightFM feature-only candidate의 source별 정규화·병합
- 선호 영화가 있는 정상 onboarding에서는 ontology rule 0.70, 장르-only 복구에서는 0.85로 rule 우선
- 콜드 상세 분석에 onboarding 근거를 유지하고 장르 의미 확장은 overview evidence로 확인
- model mapping에 없는 graph 후보를 `ontology_cold_item`으로 분리
- 의미 후보가 없을 때만 제한된 신뢰도 보정 품질 fallback
- ontology/policy 추천 이유를 `is_model_attribution=false`로 기록

정확한 정책은 [03 추천 정책](03_recommendation_policy.md)을 따른다.

합성 검증에서 hard filter 무폴백, 낮은 vote count의 popularity 과대평가 방지, negative 상한, score source 분리, 반복 후보 재정렬과 결정성을 확인했다. Build `22` read-only 검증에서 onboarding 장르 기반 20개 cold-start 후보가 모두 `ontology_cold_item`으로 분류됐고, MMR용 feature row 1,034개를 포함한 pipeline은 약 `1.87`초, policy 단계는 약 `0.007`초였다. 사용자 DB나 graph를 변경하지 않았다.

### S9. V3 serving 연결

상태: 후보 serving 구현 완료, 피드 세션·페이지 생명주기는 구조 감사로 재개방

- 기존 `app/api/v1` URL과 `RecommendationResponse` schema 유지
- model/ontology/candidate/policy/feature registry를 묶은 immutable serving bundle
- atomic `active_bundle.json` pointer와 API process memory cache
- 새 pointer나 artifact 검증 실패 시 직전 정상 bundle 유지
- 알려진 사용자의 게시된 top-150 우선 조회와 blockwise model fallback
- 신규 사용자의 feature-only LightFM과 S8 cold-start 연결
- S7/S8 결과의 정적 offset slicing과 `has_more`, source 응답 연결
- model/ontology/policy/final score와 추천 이유 diagnostics 저장
- 온보딩 갱신 시 검증된 feature-only 후보만 사용자 추천 행에 게시
- 활성 bundle이 없으면 명시적 `V3NotReadyError`

합성 hybrid artifact와 candidate snapshot으로 bundle 활성화, memory cache 재사용, 손상 pointer 거부 및 이전 bundle 유지, identity 없는 feature-only 추론, 알려진 사용자의 onboarding 변경 감지를 확인했다. 기존 pagination 테스트는 정적 목록 slicing만 검증했으므로 같은 `shuffle_seed`의 연속 페이지, 행동 후 중복·누락, 새로고침 의미의 완료 근거로 사용하지 않는다. 자세한 재감사는 [10 V1/V2 기준 V3 뼈대 감사](10_v1_v2_skeleton_audit.md)를 따른다.

## 5. 현재 바로 할 작업

```text
1. 10 문서의 V1/V2 대비 감사 결과를 바탕으로 미결정 서비스 의미 확정
2. 08 S단계의 세션·페이지 계약 결정
3. 뼈대 복구 뒤 08 A~D의 동작·운영·회귀 완결
4. 그 뒤에만 추천 정확도 고도화와 일반 성능 최적화 진행
5. 동일 144명 fixture의 결과는 기능 회귀 기준으로만 유지
6. V3 배포 시 `recsys-v3-short-term-worker` 실행과 적체·실패 관측
7. 고도화 종료 시 테스트 전용 사용자와 Redis key 정리
8. 사용자가 승인한 경우에만 명시적 보류 항목 재검토
```

현재 검증 범위에서는 NDCG와 Recall을 측정하지 않는다. 추천 정확도 비교나 policy ablation 없이 응답 시간만 성능 지표로 기록한다.

방향이 불명확한 social signal의 training eligibility와 weight 연결은 raw diagnostics를 관찰한 뒤 별도 정책 작업으로 진행한다.

ontology full build가 오래 걸리므로 schema와 exporter를 먼저 구현하되, 실제 full build는 관련 코드가 모두 준비된 뒤 한 번 수행한다.

## 6. 후순위 범위

정확도 기준선이 안정되기 전까지 다음은 구현하지 않는다.

- 랜덤 후보 quota
- 신작 강제 혼합
- long-tail 강제 혼합
- 낮은 노출량 기반 exploration

반복 감점, watched/passed 제외, 단기 취향 대응은 후순위가 아니라 V3 1차 범위다.

### Redis 단기 후보 갱신

API는 `recent_actions`를 기록하고 positive 영화·가중치·시각을 별도 pending accumulator에 넣는다. passed는 blacklist/profile만 바꾸며 scheduled candidate refresh를 만들지 않는다. DB의 `pinned_at`, `watched_at`, `passed_at`, `playlist_movies.created_at`이 durable source of truth이고 Redis는 누적 기준 판정, due schedule, processing lease와 단기 후보 cache 역할을 맡는다.

상시 worker는 기준을 충족한 due user만 계산한다. worker가 늦거나 Redis가 중단돼도 cache miss 요청은 DB graph retrieval로 fallback한다. LightFM 재학습은 online 단기 갱신과 분리하며 현재 V3 전용 일일 scheduler는 미구현이므로 검증된 수동 build/publish 절차를 유지한다.

## 7. 변경 시 확인할 경계

- API 버전과 추천 엔진 버전을 혼동하지 않는다.
- V1은 기본 정책 비교 기준이고 V2는 V1에 없거나 더 나은 부분만 선택한다.
- V2 scorer/ranker 전체를 V3에 import하지 않는다.
- OTT는 LightFM feature로 사용하지 않고 최신 DB 기반 serving filter와 정책 입력으로만 사용한다.
- 가중치 변경은 [04 LightFM 조정 지점](04_lightfm_tuning.md)에 기록한다.
- ontology schema 변경은 model artifact compatibility를 함께 변경한다.
