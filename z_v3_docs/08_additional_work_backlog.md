# 08. V3 추가 구현·보완·최적화 목록

## 1. 목적

이 문서는 현재 동작하는 V3를 기준으로 이후 작업을 한곳에서 관리한다.

- 사용자가 발견한 문제와 요구사항
- 전체 흐름 및 코드 점검에서 발견한 문제
- 정책에는 있으나 실제 추천에는 연결되지 않은 기능
- 추천 품질 고도화와 트래픽·응답 시간 최적화
- 사용자가 현재 단계에서 구현하지 않겠다고 명시한 항목

세부 설계는 각 책임 문서에서 관리한다. 이 문서는 우선순위, 선행조건, 완료 조건을 관리한다.

| 변경 대상 | 책임 문서 |
| --- | --- |
| 구현 순서와 상태 | `01_design_sequence.md` |
| 코드와 데이터 경계 | `02_implementation_guide.md` |
| 추천 정책 | `03_recommendation_policy.md` |
| 가중치와 조정값 | `04_lightfm_tuning.md` |
| 온톨로지와 build | `05_ontology_structure.md` |
| fixture와 검증 결과 | `06_test_plan.md` |
| 전체 흐름과 사용자 행동 | `07_end_to_end_flow_review.md` |
| 구조적 문제와 설계 전환의 판단 근거 | `09_design_decision_journal.md` |
| V1/V2 대비 사용자 흐름·worker 뼈대 감사 | `10_v1_v2_skeleton_audit.md` |

## 2. 분류와 실행 우선순위

`P0`~`P4`는 작업 성격을 나타내는 분류 ID다. 번호만 보고 실제 실행 순서로 해석하지 않는다.

```text
P0. 추천 로직 동작·반영 시점·운영 자동화
P1. 정책상 정의됐지만 미구현되거나 입력에 연결되지 않은 기능
P2. 추천 정확도와 정책 고도화
P3. 응답 시간, 트래픽, 자원 최적화
P4. 사용자가 명시적으로 미뤘거나 현재 보류한 기능
```

현재 사용자가 지정한 실제 실행 우선순위는 다음과 같다.

```text
A. 추천 동작과 상태 전이 완결
B. 정책상 미구현·미연결 기능 완결
C. scheduler·metadata 갱신·장애 관측 등 운영 자동화 완결
D. A~C 전체 기능 및 세부 시나리오 검증
E. 추천 품질 고도화
F. 응답 시간·동시 트래픽·자원 성능 최적화
G. 명시적 보류 기능 재검토
```

품질과 성능은 운영 중 계속 조정할 영역이므로 `A~D`가 끝나기 전에는 시작하지 않는다. 현재 작업 범위는 `A~D`다. `P4`는 기술적으로 먼저 구현할 수 있어도 누락으로 취급하지 않으며 다른 단계의 완료를 막지 않는다.

단, 기능 검증에 필요한 최소 시간 측정은 성능 최적화가 아니다. timeout, 무한 대기, cache stampede처럼 기능을 깨뜨리는 성능 결함만 `A~D`에서 수정하고 일반적인 p95 개선은 `F`로 미룬다.

작업 상태:

| 상태 | 의미 |
| --- | --- |
| `결정 필요` | 구현 전에 서비스 의미를 정해야 함 |
| `구현 가능` | 방향이 정해져 바로 구현할 수 있음 |
| `선행 필요` | schema, 실제 데이터 또는 다른 작업이 먼저 필요함 |
| `명시적 보류` | 사용자가 현재 구현하지 않기로 한 범위 |
| `기준선 완료` | 이미 구현됐으며 회귀만 유지함 |
| `점검 중` | 완료 판정을 중단하고 구조 계약을 다시 대조하는 중 |

실행 단계:

| 단계 | 포함 작업 | 현재 처리 |
| --- | --- | --- |
| S | V1/V2 뼈대 감사, `P1-02`, `P1-03`, `P0-05`, `P0-10`, `P0-11` | 최우선 진행 |
| A | production artifact 생명주기 `P0-06~09`과 S 감사 이후 남은 P0 | S 다음 진행 |
| B | `P1-01`, `P1-04~08` 정책 기능 | A 다음 진행 |
| C | `P3-06`, `P3-11~12`, 문서·retention 등 운영 마감 | B 다음 진행 |
| D | 기능 Gate, 상태 전이·장애·rollback·API 회귀 | C 다음 진행 |
| E | `P0-06`, `P2-02~09`, 유사 사용자 후보, 정량 품질 | 마지막 지속 개선 |
| F | `P3-01~05`, `P3-07~10` | E 이후 마지막 지속 개선 |
| G | `P4` | 명시적 보류 |

`P2-01`과 `P2-10`은 번호와 무관하게 D단계 기능 검증 계약으로 먼저 수행한다. 실제 사용자 artifact를 만드는 `P0-06`은 production 경로 자체는 C/D에서 점검하되, 실제 데이터 학습·비교는 E단계 진입 작업으로 둔다.

## 3. 현재 구현 기준점

다음 상태에서 추가 작업을 시작한다.

- 기존 HTTP API 경로와 `RecommendationResponse`를 유지하는 V1/V2/V3 engine plugin
- direct behavior 기반 hybrid LightFM과 blockwise exact top-150 저장(활성 100 + 예비 50)
- ontology item/user feature, 독립 단기 후보, cold-start 후보
- watched, passed, 상태, adult, OTT hard filter
- quality, negative preference, OTT, 최근성, 반복 감점, 결정적 MMR
- LightFM 점수, ontology 근거, policy effect, final score의 분리 저장
- model·ontology·candidate·policy를 묶은 atomic serving bundle
- Redis 누적 기준과 scheduled worker 기반 단기 후보 선계산
- ontology build `22`: 498.3초, node 3,756,594개, edge 12,640,874개
- item feature export: 77.8초, peak RSS 약 918MB
- seed 학습 사용자 120명과 model 생성 후 사용자 24명의 활성 fixture
- V3 단위 테스트 93개 통과. 단, 세션 연속 페이지 검증은 포함하지 않음

현재 응답 시간 기준선:

| 경로 | 평균 | p95 |
| --- | ---: | ---: |
| 알려진 사용자 warm | 3.25초 | 3.66초 |
| 신규 사용자 warm | 3.05초 | 7.42초 |
| 온보딩 변경 | 8.45초 | 9.64초 |
| 구독 OTT만 보기 | 3.93초 | 8.74초 |
| 새 process 첫 요청 | 12.99초 | 1회 측정 |

현재 검증은 후보 반환, 응답 계약, 필터 불변식과 응답 시간을 확인한 것이다. 실제 추천 정확도가 좋다는 증거는 아니며 NDCG와 Recall은 아직 측정하지 않았다.

## 4. 기준선 완료 항목

다음은 이미 반영됐으므로 새 문제 근거 없이 미구현 목록으로 되돌리지 않는다.

| 항목 | 현재 처리 |
| --- | --- |
| 후보 생성량 | 순위 후보 150개 저장, hard filter 후 상세 분석·재정렬은 최대 100개 |
| popularity와 vote count | vote confidence 적용으로 vote 1개의 높은 popularity가 quality를 지배하지 못함 |
| OTT | LightFM feature가 아니라 최신 availability filter와 policy 입력 |
| passed | WARP positive에서 제외하고 hard filter와 negative profile에 반영 |
| 단기 취향 | LightFM 후보 재정렬만 하지 않고 독립 ontology 후보 생성 |
| 반복 편향 | feature 반복 감점과 결정적 MMR 구현 |
| actor/director/theme/mood | graph, feature, profile, analyzer에 연결 |
| graph 병렬 build | 4-worker 동적 청크 큐 적용 |
| 대규모 score 계산 | dense user-movie matrix 없이 blockwise exact top-K 사용 |
| 의미 근거 분리 | ontology evidence를 LightFM의 인과 설명으로 사용하지 않음 |

## 5. P0 추천 로직 동작 보완

### P0-01. Hard filter 이후 후보 보충

- 상태: `기준선 완료`
- 완료일: `2026-08-27`
- 포착: 사용자 결과 누락 우려, `07` R1
- 기존 문제: 장기·단기 후보를 최대 100개로 합친 뒤 watched, passed, OTT, 상태 필터를 적용해 탈락한 자리를 채울 후순위 후보가 없었다.
- 경계: DB에 없는 영화와 eligibility를 통과하지 못한 영화는 보충하지 않는다.
- 결정:
  - 동일한 model·단기·cold source 병합 순위에서 100개 활성 후보와 다음 순위 50개 예비 후보를 만든다.
  - 150개에는 metadata와 OTT를 포함한 저비용 hard filter만 적용하고, 통과 순서대로 최대 100개만 ontology 상세 분석·정책 재정렬에 전달한다.
  - 예비 50개까지 소진하면 결과 부족을 허용하며 인기 영화나 미구독 OTT 영화로 강제 충원하지 않는다.
  - 전체 100개 소진 또는 새로고침 세션의 새 후보 생성은 P1-02/P1-03 결정 전까지 보류한다.
- 구현: `CANDIDATE_POOL_SIZE=100`, `CANDIDATE_RESERVE_SIZE=50`, `CANDIDATE_STORAGE_SIZE=150`을 분리했다. 사전 filter의 검사·탈락·예비 승격 수와 사유별 count를 request diagnostics에 저장한다.
- 활성 artifact: snapshot `cand-950d86d7f1f978f316f2b773`, 120명×150개=18,000개, bundle `bundle-77128ec4c5c9b5404efc3b4b`.
- 검증: V3 unit test 92개 통과. 예비 승격, OTT hard filter 유지, 예비 소진 후 부족 반환을 확인했다. 실제 known-user hybrid, 온보딩 변경, post-model cold, OTT smoke도 오류와 필터 위반 없이 후보를 반환했다.

### P0-02. watched/passed 해제 후 장기 후보 복구

- 상태: `명시적 보류`
- 포착: `07` R7
- 문제: 장기 top-150 생성 당시에 제외된 영화는 상태 해제 후 filter가 풀려도 저장 후보에 즉시 돌아오지 않는다.
- 사용자 결정: 해제 API에서 worker를 즉시 실행하지 않는다. 이후 새로고침 정책을 구현할 때 다음 추천 요청 또는 새로고침에서 현재 watched/passed 상태로 사용자 top-150을 다시 계산한다.
- 현재 동작: 저장 후보에 영화가 남아 있으면 filter 해제만으로 다시 추천 가능하다. snapshot 생성 당시에 제외돼 저장 후보에 없으면 아직 자동 복구하지 않는다.
- 재개 시점: P1-02 세션 노출과 P1-03 새로고침 의미를 결정할 때 함께 구현한다.
- 주의: 사용자 후보 재계산과 LightFM model 재학습은 다른 작업이다.
- 완료 조건: 새로고침 요청이 현재 model과 현재 제외 상태로 top-150을 재계산하고, 해제된 후보가 다시 진입할 수 있음이 검증된다.

### P0-03. 온보딩 변경 처리 통일

- 상태: `기준선 완료`
- 완료일: `2026-08-27`
- 포착: 사용자 행동 흐름 점검, `07` R6
- 문제: 선호 영화 변경은 feature-only 후보를 선계산하지만 장르 변경은 다음 요청에서 계산한다.
- 결정: V3에서는 선호 장르와 선호 영화 변경 API가 모두 사용자 정보만 저장한다. 다음 추천 요청이 온보딩 특성 변경을 감지하면 feature-only top-150을 한 번 계산해 현재 응답에 사용하고, 같은 온보딩 서명으로 DB에 원자적으로 교체 저장한다. 이후 요청은 저장 후보를 재사용한다.
- 실패 처리: 후보 저장만 실패하면 현재 요청은 메모리에서 계산한 후보로 계속 처리하고, 다음 요청에서 다시 저장을 시도한다. 후보 계산 자체가 실패하면 기존의 서명이 다른 후보를 현재 후보로 사용하지 않는다.
- OTT: OTT 변경은 LightFM 후보를 다시 만들지 않고 최신 serving context의 hard filter와 정책에 즉시 반영한다.
- 검증: V3 unit test 93개 통과. 온보딩 변경을 감지한 첫 추천 요청에서 새 feature-only 후보 저장 경로가 호출되는지 확인했다.
- 완료 조건: 장르와 선호 영화 변경 후 반영 시점과 첫 요청 처리 방식이 일치한다.

### P0-04. 단기 취향 정책 버전과 cache 호환성

- 상태: `명시적 보류`
- 사용자 결정: 현재 단기 취향 수치는 유지하며, 정책 snapshot/hash, cache namespace 버전, 변경·rollback 호환성 구현은 후속 작업으로 미룬다.
- 포착: 사용자 조정 가능 여부 질문, 코드 점검
- 현재 profile 값: 최근 30일, 최대 50개, 반감기 14일.
- 현재 갱신 값: 수집 24시간, 서로 다른 positive 영화 3개 또는 2개이면서 누적 2.0 이상, debounce 30초, 최대 대기 120초.
- 문제: profile 구성값과 갱신 실행값이 서로 다른 모듈에 있고 이 값 전체가 serving bundle hash와 Redis candidate signature에 포함되지 않는다.
- 조치:
  - `ShortTermProfilePolicy`와 `ShortTermRefreshPolicy`를 분리된 설정 객체로 정의한다.
  - version, snapshot, hash를 bundle과 diagnostics에 저장한다.
  - 후보 의미가 바뀌는 값은 Redis cache signature에 포함한다.
  - 변경 시 cache 무효화, 선제 재생성, rollback 절차를 둔다.
- 완료 조건: 값을 바꾼 뒤 이전 기준으로 생성된 cache가 조용히 재사용되지 않는다.

### P0-05. 장기·단기 반영 시점 계약

- 상태: `선행 필요`
- 선행: 같은 피드 세션의 페이지 순서와 새 세션·새로고침 의미를 먼저 확정한다. `10_v1_v2_skeleton_audit.md` F0~F3을 따른다.
- 포착: 사용자 단기 취향 갱신 질문
- 문제: 행동은 hard filter/profile에는 즉시, 단기 후보에는 worker 이후, 장기 후보와 embedding에는 더 늦게 반영된다. 이 최대 지연이 서비스 정책으로 완전히 정리되지 않았다.
- 결정:
  - 행동별 즉시 반영 범위
  - 단기 worker 반영 SLA
  - 기존 model 기반 사용자 후보 재계산 주기
  - LightFM 재학습과 신규 사용자 identity 편입 주기
- 완료 조건: pin, saved, watched, passed와 각 해제 행동의 반영 시점을 한 표로 설명할 수 있다.

### P0-06. 실제 데이터 model/candidate/bundle 생성 경로

- 상태: `선행 필요`
- 문제: 현재 활성 bundle은 144명 synthetic fixture 기준이다. 추천 시스템의 전체 동작은 검증했지만 실제 서비스 사용자 협업 패턴은 학습하지 않았다.
- 조치: 실제 데이터의 사용자 수, 행동별 pair, conflict, timestamp 누락, feature coverage를 진단한 뒤 dataset -> model -> top-150 저장 snapshot -> bundle을 생성한다. 상세 분석과 최종 노출 상한은 100개다.
- 실행 단계: production build 경로의 입력 검증·실패 처리·artifact 경계는 C/D에서 확인한다. 실제 사용자 학습과 추천 품질 비교는 E에서 수행한다.
- 완료 조건: production build와 test fixture build가 분리되고 데이터 진단 실패 시 bundle이 활성화되지 않는다.

### P0-07. 정기 학습·후보 생성·bundle 게시

- 상태: `구현 가능`
- 문제: 단기 candidate worker는 있지만 LightFM 재학습, 장기 후보 materialization, 검증, bundle 게시를 정기 실행하는 V3 scheduler는 없다.
- 조치: 각 단계를 idempotent job으로 연결하고 중복 실행 방지, retry, checkpoint, 이전 정상 bundle 유지 정책을 구현한다.
- 완료 조건: 일부 stage 실패 시 불완전한 model/graph/candidate 조합이 활성화되지 않는다.

### P0-08. 영화 metadata 변경 자동 반영

- 상태: `구현 가능`
- 포착: 사용자 요청, `05` 필수 후속 작업
- 문제: 장르·배우·감독·keyword·theme·mood 변경은 graph와 item feature를 stale하게 만들며, OTT·상태·품질 변경은 serving freshness에 영향을 준다.
- 조치:
  - 변경 필드를 graph rebuild 필요/불필요로 분류한다.
  - metadata event 또는 source fingerprint 차이를 감지한다.
  - graph, item export, model compatibility 검사, bundle 게시를 연결한다.
  - ontology와 무관한 OTT·상태·품질은 graph rebuild 없이 최신 DB 값으로 반영한다.
- 완료 조건: 변경 종류별 반영 경로와 최대 지연이 정해지고 실패 시 이전 bundle을 유지한다.

### P0-09. 화면 fallback의 V3 장애 관측

- 상태: `구현 가능`
- 포착: `07` R10
- 문제: 화면 API가 기존 추천이나 인기 목록으로 fallback하면 V3 장애가 HTTP 성공 뒤에 숨을 수 있다.
- 조치: fallback은 유지하되 engine source, 실패 사유, 횟수, 연속 지속 시간 metric과 alert를 추가한다.
- 완료 조건: API 성공과 별개로 V3 실패율과 fallback 지속 시간을 확인할 수 있다.

### P0-10. API·엔진 계약 회귀 유지

- 상태: `점검 중`
- 정정: 기존 완료 판정을 철회한다. 정적 목록의 offset slicing과 응답 schema만 검증했으며, 같은 feed session의 연속 페이지, `shuffle_seed`, 행동 후 중복·누락, 새로고침 의미는 검증하지 않았다.
- 조치: 이후 변경에서도 API V1과 추천 engine V1/V2/V3 축을 분리하고 V3 전용 HTTP route를 만들지 않는다. 추가로 `10_v1_v2_skeleton_audit.md`의 사용자 흐름 전체를 회귀 계약으로 만든다.
- 완료 조건: V1/V2가 계속 실행되고 기존 pagination, `has_more`, source, 응답 schema가 유지된다.

### P0-11. 행동 변경 후 갱신 전달 보장

- 상태: `구현 가능`
- 포착: DB 행동 상태와 Redis 갱신 예약의 책임 경계 점검
- 문제: pin, saved, watched, passed, playlist, onboarding 변경이 DB에는 반영됐지만 Redis 예약 또는 worker 전달이 실패하면 최신 DB 상태는 남아도 필요한 장기·단기 후보 갱신이 누락될 수 있다. 요청 시 DB fallback은 현재 상태 조회를 보장할 뿐 선제 갱신 작업의 유실까지 복구하지는 않는다.
- 조치:
  - 행동별 DB commit과 갱신 요청 생성의 transaction 경계를 점검한다.
  - durable outbox 또는 DB pending marker 중 하나로 갱신 필요 상태를 보존한다.
  - 전달과 worker 처리를 idempotent하게 만들고 Redis 복구 후 reconciliation job이 누락 요청을 다시 등록한다.
  - watched, passed, OTT처럼 최신 DB 값으로 즉시 적용되는 filter와 후보 재생성 필요 여부를 분리한다.
- 완료 조건: DB에 확정된 갱신 대상은 일시적인 Redis·worker 장애가 있어도 결국 처리되며, 재시도와 중복 전달이 결과를 손상하지 않는다.

## 6. P1 정책상 미구현 또는 미연결 항목

이 절은 B단계 작업이다. 입력 필드나 중간 데이터가 존재한다는 이유만으로 구현 완료로 보지 않고, 실제 요청·dataset·추천 결과 중 정책이 요구하는 경로까지 연결됐는지 확인한다. 사용자가 명시적으로 보류한 P4-05 본문 NLP는 이 단계의 완료 조건에 포함하지 않는다.

### P1-01. 소셜 행동의 추천 반영

- 상태: `결정 필요`
- 포착: 사용자 지적, `07` R5
- 현재: 게시글·좋아요·댓글을 영화로 투영하고 provenance를 남기지만 모두 `direction_unresolved`, `eligible_for_training=false`다.
- 문제: 사용자는 소셜 행동을 V3 정책 범위로 요구했고 문서에는 provisional weight 후보가 있으나 실제 LightFM 학습과 추천 결과에는 전혀 들어가지 않는다.
- 먼저 결정할 내용:
  - 영화 게시글 작성이 positive인지 중립 engagement인지
  - like/reply를 취향 방향 없이 별도 신뢰도로 사용할지
  - social-only user-movie pair를 WARP positive로 허용할지
  - self reaction, 반복 행동, 큰 playlist의 과대 반영 상한
- 권장 방향: 방향이 불명확한 행동을 강한 positive로 넣지 않는다. direct positive와 분리한 bounded social component로 시작한다.
- 구현:
  - eligibility resolver
  - action별 weight와 source cap
  - direct/social one-row-per-user-movie aggregation
  - dataset/artifact manifest와 provenance
  - social-only, cap 도달률, source별 count 진단
- 완료 조건: 어떤 소셜 행동이 왜 학습됐는지 pair 단위로 재현되며 saved/pinned보다 과도하게 커지지 않는다.

### P1-02. 세션 노출 차단 입력

- 상태: `점검 중`
- 우선순위: S단계 뼈대 최우선
- 포착: `07` R2
- 현재: policy engine에는 `session_exposed_movie_ids`가 있지만 실제 요청은 빈 집합을 전달한다.
- 결정: Redis session feed와 DB exposure event 중 어느 것을 source of truth로 사용할지, session 만료와 pagination 관계를 정한다.
- 완료 조건: refresh나 사용자 상태 변경 후에도 같은 세션 노출 정책이 유지된다.

### P1-03. `shuffle_seed` 의미 연결

- 상태: `점검 중`
- 우선순위: S단계 뼈대 최우선
- 포착: `07` R3
- 현재: API가 seed를 받지만 V3는 순위를 섞지 않고 diagnostics 식별자로만 남긴다.
- 결정: 정확도 중심 결정적 순위를 유지하며 API 의미를 수정할지, V1과 같은 안정적 session shuffle을 적용할지 정한다.
- 완료 조건: API 설명, 추천 순서, pagination 안정성이 일치한다.

### P1-04. 개별 차단 영화 계약

- 상태: `결정 필요`
- 포착: `07` R4
- 현재: `blocked_movie_ids` 입력은 있지만 실제 요청에서는 항상 비어 있다.
- 조치: 실제 차단 기능과 연결하거나 서비스에 없는 개념이면 미사용 입력과 문서를 제거한다.

### P1-05. Immutable 행동 event

- 상태: `결정 필요`
- 포착: 사용자 행동 흐름 요구, `07` R8
- 문제: 현재 pin/pass/watch와 playlist는 현재 상태 중심이라 반복 행동, 해제 후 재행동, 행동 당시 playlist 구성을 복원할 수 없다.
- 범위: event id, user, action, target, occurred_at, source, session/request id, before/after 상태, 취소 event.
- 완료 조건: 임의 cutoff 시점의 학습 dataset을 재현하고 현재 mutable 상태를 과거 사실로 사용하지 않는다.

### P1-06. 소셜 행동 시각과 playlist 과거 구성

- 상태: `선행 필요`
- 선행: P1-01, 필요 시 P1-05
- 문제: `likes.created_at`이 없고 playlist post 행동 당시 movie 구성을 복원할 수 없다.
- 조치: timestamp migration과 event-time membership을 설계한다. 기존 row의 과거 시각을 임의로 생성하지 않는다.
- 완료 조건: current snapshot build와 historical-cutoff build의 eligibility 차이가 manifest에 기록된다.

### P1-07. 상세 추천 근거 경로

- 상태: `구현 가능`
- 포착: `07` R9
- 현재: “배우 일치”, “테마 일치” 같은 유형과 점수는 있지만 어떤 원본 행동·영화·feature·edge를 거쳤는지는 최종 조회하지 않는다.
- 조치: 최종 상위 결과에 한해서 bounded 2차 evidence 조회를 수행하고 내부 진단과 사용자 문구를 분리한다.
- 완료 조건: ontology 근거를 LightFM의 인과 설명으로 표현하지 않으면서 연결 경로와 build를 재현한다.

### P1-08. 정책 출처 감사

- 상태: `점검 중`
- 포착: 사용자 기준 요구
- 조치: 행동 의미, profile, exclusion, OTT, cold-start, dynamic fill, worker safety를 V1과 먼저 비교한다. V1에 없거나 V2가 더 나은 경우만 V2를 선택하고 `v1`, `v2`, `v3_new`와 이유를 기록한다.
- 완료 조건: V2 scorer/ranker 전체를 가져오지 않고 누락 정책을 registry에서 찾을 수 있다.

## 7. P2 추천 고도화

이 절의 가중치·모델·feature·결합 방식 조정은 A~D 기능 Gate를 통과한 뒤 E단계에서 시작한다. 품질 조정으로 기능 누락을 가리지 않는다.

예외적으로 P2-01과 P2-10은 품질 튜닝이 아니라 D단계의 기능 검증 계약이다. 이 둘은 현재 점수의 우열을 확정하는 것이 아니라 후보 source, 상태 전이, filter, 근거와 결과 생성이 설계대로 연결됐는지를 검증한다.

### P2-01. 추천 결과 검토 계약

- 상태: `선행 필요`
- 선행: P0/P1의 추천 의미와 데이터 경계 고정
- 현재 범위: 정량 ranking metric이 아니라 사용자 시나리오별 후보 source, filter, score trace, 이유, 결과 존재 여부를 검토한다.
- 결정: 사용자 eligibility, DB에 없는 영화 제외, 결과 부족과 hard-filter violation 판정.
- 사용자군: stable, mixed, drift, negative-heavy, onboarding-only, sparse, cold.
- 완료 조건: V1/V3와 정책 변경 전후를 같은 입력·eligibility로 비교하고 이상 결과를 재현할 수 있다.

### P2-02. 직접 행동 weight

- 상태: `선행 필요`
- 대상: favorite `1.0`, watched `1.5`, saved `2.0`, pinned `2.0`, overlap `0.15`, cap `2.3`, recency bucket.
- 사용자 포착: saved가 실제 추천에서 충분한 비중을 갖는지 아직 검증되지 않았다.
- 원칙: signal 수, feature coverage, model 효과, policy 효과를 분리하고 한 종류의 값만 변경한다.

### P2-03. LightFM 협업 효과와 hyperparameter

- 상태: `선행 필요`
- 대상: components 32/64/128, epochs, learning rate, alpha, max sampled, loss 후보.
- 비교: collaborative identity baseline, feature-only, hybrid ontology.
- 완료 조건: 실제 사용자 data cutoff와 random seed를 고정해 협업 신호와 feature 효과를 분리한다.

### P2-04. Ontology feature ablation

- 상태: `선행 필요`
- 대상: genre, keyword, actor, director, theme, mood, actor/keyword frequency threshold와 user feature 범위.
- 문제: feature 연결 완료가 품질 개선을 보장하지 않으며 150만 feature 차원과 actor 희소성은 과적합·artifact 크기를 키울 수 있다.
- 완료 조건: feature군별 품질, coverage, train/serve 비용을 비교하고 제한은 graph 삭제가 아니라 exporter에서 수행한다.

### P2-05. 장기·단기 결합과 score 정규화

- 상태: `선행 필요`
- 대상: source별 percentile, drift 최대 0.45, floor 시작 0.60, contextual 최대 25%, personal/ontology 0.75/0.25.
- 완료 조건: 최근 새 장르 행동이 반복되면 해당 후보가 상위 결과에 진입하고 단일 행동 노이즈는 장기 후보를 지배하지 않는다.

### P2-06. 단기 취향 값 조정

- 상태: `선행 필요`
- 대상: 기간 30일, 최대 50개, 반감기 14일과 수집 구간·threshold·debounce.
- 원칙: profile 구성값과 계산 시작 조건을 별도로 실험한다.
- 비교 후보: 기간 14/30/60일, 최대 20/50/100개, 반감기 7/14/21일.
- 완료 조건: freshness, candidate 재계산량, drift 사용자 품질을 함께 비교한다.

### P2-07. Quality와 negative 정책

- 상태: `선행 필요`
- 대상: vote confidence prior 100, quality bonus 0.08, popularity reference, passed saturation 3건, negative feature weight와 상한.
- 현재 보장: vote 1개의 높은 popularity가 quality를 지배하지 않는다. 장르-only 복구 경로는 vote 0을 제외하고 vote 20 이상 후보를 우선하며, 낮은 vote 후보는 후보가 부족할 때만 보충한다.
- 남은 문제: 장르별 catalog 분포에 맞춘 신뢰 기준 20의 조정, 정상 onboarding·cold item에 별도 품질 하한이 필요한지, 적은 pass로 장르 전체가 과도하게 감점되는지.

### P2-08. Cold-start 병합 품질

- 상태: `부분 구현`
- 현재 기준: 선호 영화가 있으면 feature-only LightFM 0.30 / ontology rule 0.70, 장르-only 복구 경로는 0.15 / 0.85. 선호 영화 자체는 제외한다. 장르-only rule 내부는 의미 0.65 / 신뢰 품질 0.35이며 신뢰 vote 기준은 20이다. 온보딩 근거는 상세 ontology 분석까지 유지하고 장르 의미 확장은 실제 overview evidence로 확인한다.
- 대상: 현재 source 비중과 품질 가중치 조정, quality fallback, 실제 사용자가 충분해진 뒤 onboarding 유사 사용자 후보와 최소 지지 수.
- 후속 방향: 사용자 `1001`, `1009` 간이 분석처럼 제한된 온보딩 입력을 좁게 해석하는 문제는 단순 rule weight 증가만으로 해결하지 않는다. 유사 온보딩 사용자 후보를 별도 source와 별도 점수로 추가하고 최소 사용자 수·최소 행동 지지·fallback 조건을 둔다. 세부 판단은 `09_design_decision_journal.md` D10을 따른다.
- 완료 조건: 온보딩 정보량별로 어떤 source가 유효한지 비교하며 DB에 없는 영화는 평가에서 제외한다.

### P2-09. 후보 100개 상한과 refill 크기

- 상태: `선행 필요`
- 검증: 80/100/150에서 recall ceiling, hard filter 후 결과 수, graph 분석 시간 차이를 측정한다.
- 경계: P0-01에서는 현재 100개 상한 안에서 결과 부족을 복구한다. 이 항목은 상한 자체를 품질 실험으로 변경하는 E단계 작업이다.
- 경계: 상한을 늘려도 full graph scan이나 candidate-by-candidate query를 허용하지 않는다.

### P2-10. 사용자별 정책 시나리오 회귀

- 상태: `구현 가능`
- 범위: stable, mixed, 급격한 장르 변화, negative-heavy, 정상 onboarding, 부분 저장 복구, 행동 해제 사용자. OTT-only와 장르-only는 정상 프론트 경로가 아닌 방어 시나리오로 구분한다.
- 완료 조건: 점수만 비교하지 않고 후보 source, filter, 단기 반영, 이유, 결과 존재 여부가 사용자 흐름대로 움직이는지 기록한다.

## 8. P3 트래픽 처리와 최적화

일반적인 latency·throughput·메모리 최적화는 D단계 완료 후 F에서 수행한다. 다만 P3-06 진단 보존, P3-11 worker·scheduler 관측, P3-12 artifact 정리는 시스템을 지속적으로 검증하고 복구하기 위한 운영 기능이므로 C단계에서 먼저 완료한다.

### P3-01. 알려진 사용자 ontology 상세 집계

- 상태: `구현 가능`
- 측정: known warm 평균 3.25초, `candidate_profile_aggregate` 표본 약 2.3~2.7초.
- 조사: `EXPLAIN (ANALYZE, BUFFERS)`, relation별 row 수, 반복 scan, sort/hash spill.
- 후보: set-based aggregate, profile feature `unnest`/임시 relation, bounded evidence, materialized summary.
- 완료 조건: 추천 의미와 후보를 유지하고 before/after p50·p95·query plan을 기록한다.

### P3-02. Cache miss 단기 역조회

- 상태: `구현 가능`
- 측정: 기존 `short_term_reverse_lookup` 약 4~6초. cache format 3 선계산으로 known baseline에서는 제거됐지만 TTL 만료, 신규 사용자, worker 지연 시 요청 경로로 돌아온다.
- 후보: 만료 전 refresh, stale-while-revalidate, bounded stale 사용, source별 선계산.
- 경계: Redis 장애 시 DB fallback 자체는 유지한다.

### P3-03. 신규·온보딩 변경 graph 후보

- 상태: `구현 가능`
- 측정: cold p95 7.42초, onboarding 변경 평균 8.45초, 일부 `cold_rule_retrieval` 5~6초.
- 후보: 변경 시 비동기 선계산, token별 bounded index, set-based union, 후보 생성과 상세 분석의 2단계 분리.

### P3-04. Policy 단계 세분화

- 상태: `구현 가능`
- 측정: 표본 policy 약 0.34~0.59초.
- 조치: hard filter, quality/negative, repetition feature load, MMR, response mapping을 따로 계측해 SQL과 Python CPU를 구분한다.

### P3-05. Process 첫 model load

- 상태: `구현 가능`
- 측정: 첫 요청 약 12.99초, model artifact 약 436MB.
- 후보: process 시작 preload, readiness gate, local artifact volume, 불필요한 복사 제거, worker 수별 메모리 계획.
- 완료 조건: 첫 사용자 요청이 model load를 부담하지 않는다.

### P3-06. 진단 저장 비용과 보존

- 상태: `구현 가능`
- 현재: 요청당 약 0.01~0.03초로 주 병목은 아니지만 장기적으로 DB 크기가 증가한다.
- 결정: 전량/표본, 동기/비동기 batch, retention, 진단 실패 시 추천 성공 여부.
- 주의: 현재 A~D 검증에 필요한 baseline과 실패 진단은 D Gate가 끝날 때까지 삭제하지 않는다.

### P3-07. 실제 규모 학습·candidate build 용량

- 상태: `선행 필요`
- 현재 120명 기준 학습 7.42초와 사용자당 score 0.034초는 운영 규모를 대표하지 않는다.
- 측정: 사용자 수별 train time, candidate time, peak RSS, artifact size, publish time, checkpoint 복구.

### P3-08. Ontology build와 item export

- 상태: `구현 가능`
- 현재: graph 498.3초, item export 77.8초, export peak RSS 약 918MB.
- 조치: actor, evidence, canonical aggregate별 stage metric을 유지하고 병목을 재측정한다. 같은 source에서 graph count/hash/coverage가 일치해야 한다.

### P3-09. Graph 증분 갱신

- 상태: `선행 필요`
- 선행: P0-08 metadata 자동 반영
- 조치: factual edge 영향 범위, semantic canonical 재집계, item feature row 갱신을 분리한다.
- 완료 조건: 증분 결과가 같은 source 상태의 full rebuild와 동등하다.

### P3-10. 동시 요청과 cache stampede

- 상태: `구현 가능`
- 검증: 동일 사용자 동시 cache miss, 다수 사용자 TTL 만료, worker lease 회수, Redis 장애, DB pool 포화.
- 완료 조건: 한 사용자 계산이 중복 폭증하지 않고 timeout·fallback·queue 상한이 정의된다.

### P3-11. Worker·scheduler 운영 지표

- 상태: `구현 가능`
- 관측: pending/scheduled/processing 수, oldest due age, 성공/실패/deferred, lease reclaim, cache hit/miss/fallback, model job stage와 소요 시간.
- 완료 조건: V3 배포 시 short-term worker와 scheduler 누락 및 적체에 alert가 발생한다.

### P3-12. Artifact·fixture 정리

- 상태: `구현 가능`
- 조치: seed ownership marker, 비파괴 dry-run cleanup, active pointer 보호, model/graph/candidate/diagnostics retention을 구현한다.
- 주의: 현재 144명 fixture와 active/previous artifact는 D Gate 판정이 끝나기 전에는 실제 삭제하지 않는다.
- 완료 조건: test user와 관련 Redis key만 지우고 영화·온톨로지·실사용자 데이터는 변경하지 않는다.

## 9. P4 사용자가 명시적으로 미뤘거나 현재 보류한 항목

다음은 누락이 아니라 현재 단계에서 구현하지 않기로 명시한 범위다. A~F가 끝난 뒤 사용자 결정이 있을 때만 다시 검토하며, A~D 완료를 막지 않는다.

### P4-01. 랜덤 추천 후보 혼합

- 상태: `명시적 보류`
- 사용자 결정: 추천 정확도 기준선에 집중하기 위해 랜덤 후보군을 섞지 않는다.

### P4-02. 신작 강제 혼합

- 상태: `명시적 보류`
- 사용자 결정과 연결: 정확도와 관계없는 강제 quota는 현재 추가하지 않는다.

### P4-03. Long-tail·낮은 노출량 탐색

- 상태: `명시적 보류`
- 사용자 결정과 연결: long-tail 또는 노출량만으로 후보를 강제로 넣지 않는다.

### P4-04. 추가 다양성 quota

- 상태: `명시적 보류`
- 범위: 장르별 강제 비율, source별 임의 quota 등 정확도와 별도로 후보 구성을 강제하는 정책.
- 주의: 이미 구현된 반복 감점과 결정적 MMR은 보류 대상이 아니다.

### P4-05. 게시글 본문·hashtag 감정 및 의미 분석

- 상태: `명시적 보류`
- 사용자 결정: 방향이 불명확한 게시글을 강한 취향으로 단정하지 않고 추가 처리 정책은 나중에 세운다.
- 주의: 본문 NLP를 하지 않아도 P1-01에서 명시적 대상 영화와 bounded engagement를 사용할 수 있는지는 별도로 결정한다.

### P4-06. NDCG·Recall 정량 평가

- 상태: `명시적 보류`
- 사용자 결정: 현재 fixture 검증에서는 NDCG와 Recall을 측정하지 않고 응답 시간과 결과 생성·흐름을 확인한다.
- 재검토 시점: 실제 사용자 기반 cutoff와 held-out 정답을 신뢰할 수 있게 만든 뒤 진행한다.

## 10. 문서·운영 부채

이 항목은 해당 A~D 작업과 동시에 정리한다. 품질·성능 단계까지 미루지 않는다.

- `05` 일부의 과거 약 60분 build 기준과 현재 build `22`의 498.3초 기준을 구분한다.
- 소셜 “정책 정의”와 “실제 학습 미반영” 상태를 README, AGENTS, 02~04에서 동일하게 표현한다.
- `online`, `offline`, materialization, feature-only 같은 용어는 `07`의 한국어 설명을 먼저 쓰고 전문 용어를 병기한다.
- 단기 profile 정책과 refresh 실행 정책을 문서에서도 별도 표로 관리한다.
- active/previous/failed ontology, model, candidate, bundle의 보존 수와 삭제 순서를 정한다.
- 상세 진단 row의 보존 기간과 개인정보 최소화 범위를 정한다.
- 구현 중 새로 발견한 구조 문제와 선택 근거는 `09_design_decision_journal.md`에 기록하고, 해결되지 않은 실행 항목은 이 문서에 ID를 부여한다.
- DB와 Redis를 각각 어떤 상태의 source of truth로 사용하는지, 장애 시 어느 데이터로 복구하는지를 작업별로 명시한다.

## 11. 실행 순서

현재는 S단계와 1~8까지를 먼저 끝낸다. 이 구간에서는 정확도 점수나 일반적인 응답 시간 개선을 목적으로 값을 조정하지 않는다.

```text
S1. V1 사용자 흐름과 worker 안전장치를 기능 단위로 전수 감사
S2. V2는 V1에 없거나 실제 연결이 더 나은 부분만 별도 표시
S3. 추천 세션, 연속 페이지, 행동 반영, 100개 소진, 새로고침 계약 확정
S4. 행동 commit 후 갱신 전달과 유실 복구 계약 확정
S5. 정적 slicing 테스트의 기존 완료 판정을 철회하고 필요한 회귀 시나리오 정의

1. S단계에서 확정한 피드 뼈대 복구
2. V1 lock/retry/이전 결과 유지 기준으로 V3 production scheduler 완성
3. metadata 갱신과 fallback 관측을 artifact 생명주기에 연결
4. P1 정책상 미구현·미연결 입력의 결정과 구현
5. P3-06 진단 보존, P3-11 운영 지표, P3-12 artifact·fixture 정리 완성
6. 문서, version, source of truth, retention과 rollback 절차 일치 확인
7. P2-01·P2-10 계약으로 전체 사용자 흐름과 상태 전이 검증
8. production build 경로의 입력 검증·부분 실패·비활성 artifact 처리를 검증하고 A~D Gate 판정

9. E: 실제 사용자 artifact 생성 후 direct weight -> LightFM -> ontology feature -> 결합/policy 순서로 품질 고도화
10. F: 요청 경로, 동시 트래픽, 학습·build 자원과 응답 시간 최적화
11. G: 사용자가 승인한 경우에만 P4의 탐색, 본문 분석, NDCG·Recall을 재검토
```

1~8 수행 중 기능 결함을 재현하는 데 반드시 필요하지 않다면 다음 항목을 같이 바꾸지 않는다.

- 행동 weight, LightFM hyperparameter, ontology feature scale, policy weight
- P0-08에 필요하지 않은 graph schema 확장과 검증 목적이 아닌 active serving bundle 교체
- P0-04 호환성 보완을 넘어서는 단기 threshold 재조정과 불필요한 Redis cache format 변경
- 저장 후보 150개와 상세 처리·노출 상한 100개, cold-start source 비중, quality·negative threshold
- 일반적인 p95 개선을 위한 SQL·cache·model load 최적화

## 12. 완료 Gate

### 12.1 공통 불변식

- V1/V2를 제거하지 않고 기존 API 계약을 유지한다.
- 정책 출처를 `v1`, `v2`, `v3_new` 중 하나로 기록한다.
- model, ontology, policy, short-term 설정 version을 진단에서 식별할 수 있다.
- hard filter를 위반하거나 DB에 없는 영화를 결과 보충용으로 사용하지 않는다.
- graph 전체 scan, 후보별 graph query, dense user-movie matrix를 만들지 않는다.
- 동일 fixture에서 실패, 빈 결과, pagination, watched/passed/OTT 위반을 확인한다.
- 성능 변경은 before/after p50, p95, max와 단계별 시간을 기록한다.
- 추천 의미 변경은 응답 시간 개선만으로 승인하지 않는다.
- seed와 실사용자, test artifact와 active artifact 경계를 유지한다.

### 12.2 A~D 완료 조건

A~D는 다음 항목이 모두 충족돼야 완료로 판단한다.

- P0와 P1 항목은 구현·검증되거나, 서비스 의미상 제외한다는 사용자 결정과 근거가 기록돼 있다. 단순히 입력을 빈 값으로 전달하는 상태는 완료가 아니다.
- pin, saved, watched, passed, playlist, onboarding, OTT의 생성·변경·해제·재설정 상태 전이가 각각 검증된다.
- 즉시 filter/profile 반영, 단기 후보 갱신, 장기 후보 재생성, model 재학습의 서로 다른 반영 시점과 최대 지연을 설명할 수 있다.
- Redis 중단·복구, DB fallback, worker retry·lease 회수, 중복 전달, 손상·만료 cache에서 데이터 유실 없이 복구한다.
- scheduler 재시작, 중복 실행, stage 부분 실패, validation 실패에서 active bundle은 이전 정상 조합을 유지한다.
- metadata 변경 유형별로 최신 DB 조회, graph/item feature rebuild, model 호환성 검사 중 필요한 경로만 실행한다.
- 알려진 사용자, 단기 변화 사용자, negative-heavy, onboarding-only, cold, 행동 해제 사용자가 결과를 만들고 source·filter·근거가 설계대로 기록된다.
- V1/V2/V3 engine switch, pagination, `has_more`, source와 `RecommendationResponse` 회귀를 통과한다.
- DB에 없는 영화, watched/passed, 상태 비활성, OTT hard filter 위반 영화가 refill이나 fallback으로 다시 들어오지 않는다.
- 문서, 설정 version, 운영 지표, retention, rollback 절차가 실제 코드와 일치한다.
- 품질 가중치 조정이나 latency 최적화로 기능 결함을 우회하지 않았다.

### 12.3 E·F 진입 조건

- E는 12.2가 통과되고 실제 사용자 dataset의 적합성·개인정보·cutoff 기준이 준비된 뒤 시작한다.
- F는 12.2가 통과되고 동일한 기능·후보 의미를 유지하는 before/after 측정 환경이 준비된 뒤 시작한다.
- E와 F에서 발견된 기능 누락은 해당 최적화를 중단하고 A~D 항목으로 되돌려 처리한다.

## 13. 다음 결정 대상

가장 먼저 S단계의 추천 세션 뼈대를 확정한다.

1. 같은 `shuffle_seed`가 어떤 추천 세션을 뜻하는지
2. 첫 20개와 다음 20개 사이에 순위를 고정할 범위
3. Pass/Watch hard filter와 Pin/Save 취향 반영의 세션 경계
4. 이미 노출한 영화와 예비 후보 50개의 페이지 진행 방식
5. 100개 소진과 새로고침이 새 세션을 만드는 기준
6. `offset`, `limit`, `has_more`, `source`의 세션 기준 API 계약

같은 S단계에서 장기·단기 반영 시점과 DB 행동 commit 후 Redis·worker 전달 실패 복구를 확정한다. 그다음 V1 worker 안전성을 기준으로 production scheduler, metadata 갱신, fallback 관측을 완성한다. 이 뼈대가 완료된 뒤에만 소셜 행동, 개별 차단, event 이력과 상세 근거를 진행한다.

현재 다음 작업으로 잡지 않는 항목은 actual weight·LightFM hyperparameter·ontology feature ablation·cold-start 비중·유사 사용자 품질 조정과 일반적인 latency 최적화다. 이들은 A~D가 끝난 뒤 E와 F에서 진행한다.
