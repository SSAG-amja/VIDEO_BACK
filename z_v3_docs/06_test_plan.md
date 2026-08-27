# V3 기준선 테스트 계획

## 1. 목적

이 테스트는 실제 DB·Redis·LightFM artifact·ontology build `22`·policy engine·기존 API 계약을 연결해 V3가 설계된 경로로 동작하는지 확인하고 사용자별 응답 시간 기준선을 남긴다. 결과는 이후 추천 고도화의 동일 fixture 비교 기준으로 사용한다.

현재 범위에서 NDCG, Recall, 사용자 정답 기반 정확도 비교와 policy ablation은 수행하지 않는다.

## 2. 비파괴 경계

- 영화, 장르, OTT와 V2/V3 ontology graph row를 생성·수정·삭제하지 않는다.
- build `22`는 model·candidate·policy와 serving bundle로 검증될 때만 V3 범위에서 활성화한다.
- 기존 API path와 `RecommendationResponse`를 변경하지 않는다.
- test user는 `v3seed-*@pinlm.test`로만 식별한다.
- Redis 전체 DB를 flush하지 않고 test user의 recent action, blacklist, profile version, pending accumulator, scheduled/processing membership과 V3 short-term candidate key만 변경한다.
- model, candidate snapshot과 test user는 고도화 반복이 끝날 때까지 유지한다.

## 3. Fixture 구성

전체 사용자는 144명이다. 120명을 먼저 만들어 hybrid LightFM model mapping에 포함하고, model과 candidate snapshot을 고정한 뒤 24명을 추가해 identity가 없는 runtime 경로를 검증한다.

### 3.1 학습 사용자 120명

| 유형 | 인원 | 핵심 조건 |
| --- | ---: | --- |
| stable | 72 | onboarding과 장·단기 positive가 같은 취향군 |
| mixed | 24 | 기본 취향과 인접 취향군을 함께 사용 |
| drift | 12 | 60~180일 전 기존 취향과 최근 14일 반대 취향을 분리 |
| negative_heavy | 12 | positive는 유지하되 최근 passed 비율을 높임 |

취향군은 다음 6개다.

1. action·crime·thriller
2. romance·drama·comedy
3. horror·mystery·thriller
4. animation·family·adventure
5. science fiction·fantasy·adventure
6. documentary·history·war

영화는 SQL 작성 시 고정 ID를 나열하지 않는다. 추천 가능 catalog, build `22`의 active movie node, 장르, `vote_count >= 20`을 만족하는 영화 중 vote count와 popularity 기준으로 정렬해 cohort별 120개 pool을 만든다. 사용자 간 anchor는 공유하되 mixed cohort의 동일 영화 중복은 제거한다.

training seed dry-run 기준 데이터는 다음과 같다.

| 데이터 | 건수 |
| --- | ---: |
| 사용자 | 120 |
| favorite | 600 |
| saved | 960 |
| `user_interactions` row | 2,567 |
| 게시글 | 24 |
| 좋아요 | 96 |
| 댓글 | 48 |

각 사용자는 최소 12개의 서로 다른 positive 영화를 가진다. pin과 passed는 동일 DB 상태에 함께 존재하지 않으며 passed와 positive가 겹치는 후보는 seed 집계 전에 제거한다.

### 3.2 커뮤니티 overlay

학습 사용자 중 24명이 각각 게시글 1개를 작성한다. 16개는 영화 글이고 8개는 공개 playlist 글이다. 각 사용자는 다른 사용자의 글 4개에 좋아요를 누르고 2개에 댓글을 작성한다.

현재 social projector는 이 데이터를 provenance diagnostics로만 기록하며 모든 signal이 `eligible_for_training=false`다. 따라서 커뮤니티 activity를 LightFM 점수 변화로 해석하지 않는다. playlist 글과 댓글은 해당 시점 이전에 playlist에 들어 있던 영화에만 `1/N` 투영한다. timestamp가 없는 like는 current snapshot 진단에만 포함한다.

### 3.3 학습 후 사용자 24명

| 유형 | 인원 | 예상 runtime 경로 |
| --- | ---: | --- |
| genre + favorite | 8 | feature-only LightFM + ontology cold-start |
| genre only | 8 | genre feature-only + ontology cold-start |
| OTT only | 4 | 의미 profile 부재, subscribed policy/quality fallback |
| empty profile | 4 | no-profile quality fallback |

두 번째 seed는 기존 model mapping에 포함된 사용자 6명의 onboarding genre와 favorite도 변경한다. 이 사용자는 identity가 알려져 있어도 stored feature와 현재 onboarding이 다르므로 stale snapshot 대신 feature-only 갱신 경로를 사용해야 한다.

## 4. 단계별 실행

정확한 명령과 cleanup은 [`tests/v3_user_seed/README.md`](../tests/v3_user_seed/README.md)를 따른다.

```text
P0. DB와 Redis 시작, build 22 success 확인
P1. 120명 training SQL dry-run
P2. 120명 training SQL commit
P3. production helper 기반 Redis hydration과 정합성 검사
P4. hybrid LightFM model 학습 및 artifact reload 검증
P5. known-user exact top-150 저장 snapshot 생성·DB 게시
P6. model/ontology/candidate/policy serving bundle 활성화
P7. 24명 cold SQL commit 및 6명 known onboarding 변경
P8. Redis 재동기화
P9. production V3 cold-start refresh와 short-term scheduled worker로 후보 준비
P10. 동작 invariant 검사
P11. cold/warm 사용자별 응답 시간 측정
P12. 결과 기록 후 동일 fixture로 추천 고도화 반복
P13. 고도화 종료 후 test data cleanup
```

P4 이전에 cold user를 생성하면 model identity에 포함되므로 순서를 바꾸면 안 된다. P2를 다시 실행하면 dataset hash가 달라질 수 있으므로 P4~P6 artifact를 모두 다시 만들어야 한다.

## 5. Redis 검증

interaction API는 pinned, passed, watched, saved를 DB commit 후 `record_interaction_cache`에 전달한다. playlist 직접 변경과 행동 삭제 경로도 공통 recommendation profile version을 증가시킨다. seed도 같은 helper를 사용해 현재 DB state를 시간순으로 재생한다.

검증 조건:

- seed user별 `recent_actions`가 최신순이며 최대 50개다.
- watched와 passed의 합집합이 Redis blacklist와 정확히 일치한다.
- blacklist가 비어 있지 않으면 TTL이 양수다.
- saved/pinned/watched는 최근 24시간 positive accumulator에 영화별 최대 가중치로 기록한다.
- positive 기준 미달 사용자는 계산하지 않고, 기준 성립 사용자는 30초 debounce와 2분 상한 후 scheduled queue에서 선점한다.
- passed/OTT 변경은 독립 단기 후보 재생성을 예약하지 않는다.
- worker 성공 후 processing lease와 처리한 pending accumulator가 제거되고, 비정상 종료 작업은 lease 만료 후 재선점된다.
- worker가 만든 short-term candidate payload의 build/user/format signature가 일치할 때만 hit다.
- cache TTL은 저장 시점부터 6시간 이상, 사용자별 jitter를 포함해 6시간 30분 이하이며 벽시계 경계에서 일괄 만료되지 않는다.
- 다른 user key와 Redis DB 전체를 삭제하지 않는다.
- Redis가 없어도 DB profile과 short-term graph retrieval 경로가 유지되고 blacklist만 빈 집합으로 fallback한다.

Redis blacklist는 V3 hard filter에 사용된다. `recent_actions` 문자열은 감쇠 계산에 직접 사용하지 않으며 pending accumulator는 계산 시점만 결정한다. DB timestamp가 short-term score의 source of truth이고 Redis 장애 시 요청이 DB retrieval로 fallback한다.

## 6. 동작 Gate

### G1. Dataset과 model

- model user count가 120명이다.
- positive pair가 모든 training user에 존재한다.
- passed는 WARP positive coordinate에 없다.
- watched와 passed exclusion이 dataset manifest와 candidate materializer에 전달된다.
- social eligible count는 현재 `0`이고 raw/deferred count만 기록된다.
- model artifact의 dataset, ontology, item/user feature hash와 reload prediction이 일치한다.

### G2. Candidate와 bundle

- 알려진 사용자별 저장 후보는 최대 150개이고 movie 중복과 non-finite score가 없다. hard filter 이후 상세 분석 입력은 최대 100개다.
- materialization 전에 watched와 passed를 제외한다.
- DB 게시 row의 model build ID와 candidate snapshot ID가 활성화 입력과 일치한다.
- bundle이 model build, ontology build `22`, feature registry와 policy config를 함께 검증한다.
- 손상 pointer는 새 process에서 `V3NotReadyError`, 기존 process에서 직전 정상 bundle 유지로 처리된다.

### G3. Online 추천

상태: 단일 요청 불변식은 확인했지만 세션 연속 페이지 항목은 미검증이다.

- 모든 응답은 최대 100개이며 같은 피드 세션의 page 간 순서와 `has_more`가 일관된다. 이 항목은 `10_v1_v2_skeleton_audit.md`의 시나리오를 통과하기 전까지 미완료다.
- watched, passed와 Redis blacklist 영화는 최종 결과에 없다.
- `subscribed_only` 결과는 사용자가 구독하고 현재 streaming 중인 OTT 영화만 포함한다.
- drift 사용자의 diagnostics에는 조건이 충족될 때 `short_term_context` source가 나타난다.
- cold 사용자는 `feature_only_model`, `cold_start`, `ontology_cold_item` 또는 quality fallback 경로로 분류된다.
- model raw, normalized long/short-term, ontology, policy adjustment와 final score가 분리 기록된다.
- ontology 추천 이유의 `is_model_attribution`은 항상 `false`다.
- 동일 bundle/profile 요청은 random 후보 교체 없이 결정적 순서를 유지한다.

## 7. 응답 시간 측정

정확도 지표 대신 실제 사용자별 elapsed time을 기록한다. 측정 대상은 V3 adapter 진입부터 DB profile, candidate 조회, ontology analyzer, policy, diagnostics commit과 response 생성이 끝날 때까지다.

다음 시간을 섞지 않고 분리한다.

| 측정 | 방법 |
| --- | --- |
| process cold load | 새 process에서 최초 bundle/model load 1회 |
| known warm | model cache가 준비된 뒤 training user 120명 각각 1회 |
| cold warm | cold user refresh가 끝난 뒤 24명 각각 1회 |
| subscribed-only | OTT가 있는 대표 사용자 24명 각각 1회 |
| onboarding mutation | model 학습 후 onboarding이 변경된 known user 6명 각각 1회 |

기본 측정은 동시성 없이 순차 실행해 사용자 profile 차이를 관찰한다. 각 row에는 user ID, fixture type, mode, candidate source, result count, elapsed seconds, bundle/model/snapshot ID와 오류를 기록한다. 요약은 사용자 1명당 평균, 중앙값, p95, 최댓값과 실패 수를 기록한다.

첫 기준선은 절대 합격 시간을 임의로 고정하지 않는다. cold load와 warm path를 분리하고, 이후 고도화가 동일 fixture의 warm p95를 악화시키면 원인을 확인한다.

## 8. 결과와 반복

실행 결과는 `z_v3_docs/diagnostics/`에 timestamp가 포함된 JSON으로 저장한다. binary model과 candidate snapshot은 `assets/ml_models/v3/`에 두고 Git에 추가하지 않는다.

첫 실행 후에도 fixture를 삭제하지 않는다. 추천 목록, source 분포, filter reason, score trace와 latency를 분석해 LightFM feature/weight, short-term retrieval, ontology score와 policy를 수정하고 동일 사용자를 다시 실행한다. cleanup은 고도화 반복 종료 시 한 번 수행한다.

## 9. 첫 실행 결과

2026-08-25에 P0-P11을 완료했다. 결과 원본은 [`diagnostics/v3_online_baseline_20260825T091923Z.json`](diagnostics/v3_online_baseline_20260825T091923Z.json)이다.

- training 120명과 post-model cold 24명을 유지한다.
- hybrid model, 12,000개 known-user 후보와 serving bundle을 활성화했다.
- online request run 181개가 모두 성공했고 invariant 및 page-order 실패는 0건이다.
- process cold load는 13.72초다.
- known warm은 평균 7.04초, p95 8.65초다.
- cold warm은 평균 2.90초, p95 7.00초다.
- subscribed-only는 평균 7.00초, p95 8.43초다.
- onboarding mutation은 평균 8.61초, p95 9.85초다.

초기 top-100 Candidate materialization은 사용자당 평균 0.034초였으므로 당시 주요 병목은 후보 저장 계산이 아니라 요청 시 profile, ontology 분석, policy와 diagnostics 저장 경로에 있었다. 현재 top-150 저장 구조의 성능은 별도 측정값으로 갱신해야 하며, 이 과거 수치를 그대로 적용하지 않는다.

## 10. 단기 후보 materialization 적용 결과

설계 누락을 보완한 뒤 `diagnostics/v3_online_baseline_20260825T102610Z.json`으로 동일 fixture를 재검증했다.

- 이전 cache 기준 V3 unittest `75개` 통과
- 본 측정 175건과 정적 상태의 별도 page check는 성공했다. 이 page check는 행동 변경, 노출 기록, `shuffle_seed` 새 세션을 검증하지 않았다.
- 응답 0건과 candidate pool 0건 모두 `0`
- `known_user_hybrid` 132건 모두 short-term cache `hit`
- known warm 평균 `3.20초`, p95 `3.40초`로 이전 `7.04초`, `8.65초`에서 감소
- known-user 정상 hybrid 114건 평균 `2.93초`
- cold-start 43건은 short-term cache 대상이 아니며 onboarding 변경 known user는 평균 `8~9초`로 별도 병목이 남음

단기 역조회 반복은 제거됐지만 최대 100개 후보의 ontology analyzer와 cold-start graph 집계는 결과 점수·필터·근거에 영향을 주므로 아직 요청 경로에 남아 있다. 다음 성능 작업은 이 bounded 집계를 사전 materialize하거나 두 단계로 축소하는 방향으로 진행한다.

`diagnostics/v3_online_baseline_20260825T115455Z.json`은 기능 불변식은 모두 통과했지만 성능 기준에서는 폐기한다. 당시 cache format 2가 6시간 벽시계 bucket을 signature에 포함해 측정 도중 경계를 지난 50명이 동시에 miss/recompute했다. format 3은 이 bucket을 제거하고 저장 시점 TTL과 사용자별 jitter로 교체했다.

## 11. 갱신 정책과 cache format 3 결과

최종 회귀 결과는 [`diagnostics/v3_online_baseline_20260825T121801Z.json`](diagnostics/v3_online_baseline_20260825T121801Z.json)이다.

- 당시 전체 V3 unittest `81개`와 갱신 정책 집중 테스트 `13개`가 통과했다. 이후 콜드스타트 정책 회귀를 추가한 현재 전체 suite는 `89개`다.
- Redis 동기화는 seed 144명, 현재 행동 3,533건, blacklist 영화 1,688건을 검증하고 positive 상태가 있는 120명만 예약했다.
- 3개 worker가 각각 40명씩 계산했다. 총 120명 모두 refresh 성공, 실패·재예약·기준 미달은 `0`이고 종료 후 scheduled/processing queue는 모두 `0`이다.
- online 175건과 정적 상태 page check는 실패·불변식 위반·빈 응답이 `0`이다. 세션 연속성 완료 근거로는 사용하지 않는다.
- `known_user_hybrid` 132건의 short-term cache 상태는 전부 `hit`이며 측정 중 재계산은 `0`이다.
- known warm 평균 `3.25초`, p95 `3.66초`이고 cold warm 평균 `3.05초`, p95 `7.42초`다.
- onboarding 변경은 평균 `8.45초`, subscribed-only는 평균 `3.93초`다. 남은 긴 꼬리는 갱신 정책이 아니라 cold/onboarding ontology 집계와 일부 policy/analyzer 경로다.

이 결과를 단기 후보 갱신 정책의 현재 성능 기준선으로 사용한다. NDCG와 Recall은 이 검증 범위에 포함하지 않았다.
