# V3 추천 시스템 문서

이 디렉터리는 V3 추천 엔진의 현재 설계 기준을 주제별로 관리한다.

## 빠른 탐색

| 순서 | 문서 | 답하는 질문 |
| ---: | --- | --- |
| 1 | [설계 및 구현 순서](01_design_sequence.md) | 지금 어느 단계이고 다음에 무엇을 구현하는가? |
| 2 | [구현 방식](02_implementation_guide.md) | 코드와 데이터 파이프라인을 어떻게 구성하는가? |
| 3 | [추천 정책](03_recommendation_policy.md) | 협업 신호를 어떻게 만들고 후보를 필터링·재정렬하는가? |
| 4 | [LightFM 가중치 및 조정 지점](04_lightfm_tuning.md) | 어떤 값이 어디에 있고 무엇을 조정해야 하는가? |
| 5 | [온톨로지 구조](05_ontology_structure.md) | 그래프 node, relation, evidence, build는 어떻게 구성되는가? |
| 6 | [기준선 테스트 계획](06_test_plan.md) | 단계별 seed, Redis, 동작 gate와 응답 시간을 어떻게 검증하는가? |
| 7 | [전체 추천 흐름 점검](07_end_to_end_flow_review.md) | 전체 구조와 사용자 행동별 적용 흐름에서 무엇을 다시 판단해야 하는가? |
| 8 | [추가 구현·보완·최적화 목록](08_additional_work_backlog.md) | 현재 V3 기준점에서 무엇을 어떤 순서로 추가 작업하는가? |
| 9 | [문제에서 설계 전환까지의 판단 기록](09_design_decision_journal.md) | 왜 기존 방식 대신 현재 방법을 선택했는가? |
| 10 | [V1/V2 기준 V3 뼈대 감사](10_v1_v2_skeleton_audit.md) | 실제 피드 흐름에서 V1의 기본 동작을 빠뜨리지 않았는가? |
| 11 | [추천 품질 기준점](11_recommendation_quality_baseline.md) | 장기·단기 추천 결과에서 어떤 품질 문제가 확인됐고 무엇부터 개선하는가? |
| 12 | [추천 품질 문제 분석 및 개선 계획](12_recommendation_quality_improvement_plan.md) | 품질 문제가 어느 단계에서 발생하며 어떤 순서와 완료 조건으로 수정하는가? |

## 읽는 순서

처음 인수인계받은 경우:

```text
README
-> 10 V1/V2 기준 V3 뼈대 감사
-> 01 설계 및 구현 순서
-> 07 전체 흐름 점검
-> 08 추가 작업 목록
-> 09 주요 판단 과정
-> 11 추천 품질 기준점
-> 12 추천 품질 개선 계획
-> 작업 대상에 따라 02~06 중 하나
```

현재 후보·점수·필터 파이프라인은 구현됐지만, 같은 피드 세션의 연속 페이지와 `shuffle_seed` 의미는 완료 판정을 철회했다. `10` 문서에는 확인된 결함과 작업 제안을 분리해 기록했으며, 새 구현을 시작하기 전에 `08`의 S단계 서비스 의미를 먼저 확정해야 한다.

정책을 수정하는 경우:

```text
03 추천 정책
-> 04 LightFM 조정 지점
-> app/services/recsys/v3/policy/policy_registry.py
-> app/services/recsys/v3/config.py
```

온톨로지를 수정하는 경우:

```text
05 온톨로지 구조
-> 02 구현 방식의 build/artifact 경계
-> V2 graph/build 코드는 원천 및 최적화 형태만 참고
```

## 현재 구현 상태

완료:

- API와 추천 엔진 버전 분리
- `RECOMMENDATION_ENGINE` 기반 V1/V2/V3 adapter registry
- V3 패키지와 job scaffold
- Python 3.11 LightFM dependency gate
- V3 정책 출처 registry
- saved/pinned/watched/passed/favorite snapshot dataset builder
- LightFM interaction/sample-weight sparse matrix 생성
- 게시글·좋아요·댓글의 협업 신호 투영 정책 정의
- 게시글·좋아요·댓글 raw signal projector, playlist `1/N`, provenance diagnostics
- feature source/consumer registry와 onboarding·long-term·short-term profile schema
- graph edge provenance 기반 long-term/short-term runtime profile builder
- OTT를 LightFM feature에서 분리한 serving context 계약
- V2/V3 ontology schema별 build/activation 경계와 evidence migration
- V3 relation registry, person 역할 통합, OTT 제공 방식 분리
- immutable V3 graph builder와 source fingerprint pipeline
- semantic evidence 및 bounded-union canonical edge 집계
- graph stage metrics, validation, 4-worker 동적 청크 factual edge build
- V3 전용 ontology asset `0.2.0`과 실제 DB source coverage validator
- 전체 30 theme·16 mood의 명시적 derivation 경로
- 중복 ontology index 약 1.07GB 제거와 stage별 planner 통계 갱신
- ontology item feature CSR exporter, pruning diagnostics와 build-bound manifest
- bounded onboarding user feature exporter와 ontology hybrid LightFM artifact
- blockwise exact top-150 저장(활성 100 + 예비 50), watched/passed exclusion, checkpoint candidate snapshot과 원자적 DB 게시 경계
- 독립 short-term ontology retrieval, source 정규화·병합, bounded ontology analyzer와 최신 OTT evidence
- hard eligibility, 분리 score trace, bounded 정책 조정, 결정적 MMR policy engine
- onboarding ontology·LightFM feature-only cold-start 병합과 `ontology_cold_item` 분리
- immutable serving bundle 활성화, 메모리 cache, S7/S8 online orchestration과 기존 응답 계약 연결
- 24시간 positive 누적·threshold·debounce scheduled worker 기반 short-term 후보 선계산과 DB fallback

미구현:

- 같은 피드 세션의 연속 페이지, 노출 기록, `shuffle_seed`와 새로고침 의미 연결
- social raw signal 방향 판정과 LightFM training eligibility/weight 연결
- 실제 서비스 사용자 데이터 기반 학습과 추천 품질 평가
- ontology analyzer와 cold-start online 집계의 추가 응답 시간 최적화
- 상세 evidence path 기반 사용자 설명

V3 adapter는 serving pipeline을 호출한다. 검증된 active bundle이 없거나 손상된 새 process에서는 `V3NotReadyError`를 발생시키며 V1 결과로 조용히 위장하지 않는다. 현재는 아래 seed baseline bundle이 활성 상태다.

V3 graph build `22`는 498.3초에 node 3,756,594개, edge 12,640,874개, evidence 2,078,395개로 완료됐다. Full-catalog Item Feature Export는 77.8초에 `1,176,540 x 1,502,427`, `nnz=10,505,033` CSR을 생성했고 CSR 88,746,428 bytes, peak RSS 918,224,896 bytes를 기록했다. 상세 cardinality와 coverage는 `diagnostics/item_feature_export_build_22.json`에 있다. Build `22`는 단독 활성화하지 않으며 현재 검증된 model·candidate·policy와 하나의 serving bundle로 활성화돼 있다.

S3 identity-only trainer와 불변 artifact save/load 검증은 구현됐다. 실제 DB의 학습 catalog와 build `22` movie node는 모두 `1,176,540`편으로 일치한다. identity-only는 구현 검증용 기준선으로 유지하되 현재 범위에서는 실제 artifact를 별도로 만들거나 hybrid와 정확도 비교하지 않는다.

S4 runtime profile builder도 구현됐다. positive/negative 행동, watched/passed exclusion, 최근 30일 최대 50개 단기 profile, 관계군 정규화, feature cap/top-K, edge provenance와 drift component를 분리한다. Build `22` 표본의 6개 관계군 조회는 약 `0.003`초였다. Seed 144명의 stable/mixed/drift/negative/cold profile을 실제 online baseline에 연결했다.

S5 hybrid LightFM trainer와 artifact도 구현됐다. S2 item CSR과 bounded onboarding user CSR을 연결하며 mapping/build/source/export/registry 호환성을 학습과 재로딩 양쪽에서 검증한다. Seed 학습 사용자 120명으로 `hybrid-fcd0e51b5e6b-89de5ebb1943-bf89dc0a3ba9-159ca3769e3b-f321aac0c3a1-7b869d3b` artifact를 생성했고 저장 전후 prediction 검증이 일치했다. 학습 자체는 7.42초였으며 artifact 크기는 약 436MB다.

S6 blockwise materializer도 구현됐다. 전체 user-movie dense matrix 없이 user/item block만 계산하고 watched/passed 제외, 결정적 동점, 사용자 실패 격리, 재시작 checkpoint, 불변 snapshot과 트랜잭션 DB 게시를 지원한다. 초기 top-100 기준선은 120명 12,000개였고, 현재는 hard filter 보충을 위해 top-150으로 확장해 `cand-950d86d7f1f978f316f2b773`에 120명 18,000개를 게시했다. 상세 분석과 최종 정책 입력은 최대 100개로 유지한다.

S7 short-term retrieval과 bounded analyzer도 구현됐다. 최근 positive feature에서 LightFM과 독립 후보를 생성하고 source별 percentile 정규화와 drift floor로 최대 100개를 만든 뒤, bounded graph 집계와 최신 OTT 조회로 유형 점수를 계산한다. Build `22`의 가장 넓은 장르 기준 read-only pipeline은 약 1.06초였다.

S8 policy engine과 cold-start도 구현됐다. watched/passed/adult/session/status/OTT hard filter, personal·ontology·policy 분리 trace, vote-count 신뢰도 품질, bounded negative/OTT/recency, genre/actor/director/theme/mood 반복 감점과 결정적 MMR을 적용한다. 정상 온보딩은 선호 장르와 선호 영화에서 만든 ontology 규칙 후보 70%, feature-only LightFM 후보 30%로 시작한다. 장르만 남은 방어 경로는 규칙 후보 85%, model 후보 15%를 적용하고, 장르에서 확장한 theme·mood는 실제 영화 overview evidence가 있을 때만 사용한다. model mapping에 없는 graph 영화는 `ontology_cold_item`으로 기록하고 의미 후보가 없을 때만 품질 fallback을 사용한다.

S9 serving 코드는 model artifact, ontology build, candidate snapshot, feature registry, policy config를 하나의 immutable bundle로 검증한 뒤 `active_bundle.json`만 원자 교체한다. API process는 hybrid artifact를 메모리에 cache하고 잘못된 새 pointer는 거부하면서 직전 정상 bundle을 유지한다. 알려진 사용자는 게시된 top-150을 읽고 hard filter 통과 순서로 최대 100개만 상세 분석한다. 신규 사용자는 identity 없이 onboarding feature-only LightFM을 계산해 S8 cold-start와 결합한다. HTTP schema는 유지했지만 피드 세션과 페이지 생명주기는 미완료다.

현재 candidate snapshot은 `cand-950d86d7f1f978f316f2b773`, serving bundle은 `bundle-77128ec4c5c9b5404efc3b4b`다. 전체 V3 단위 테스트 93개가 통과했지만 세션 연속 페이지 회귀는 포함하지 않는다. 기존 known warm 평균 3.25초, p95 3.66초는 요청 시간 기준선이며 피드 뼈대 완료나 추천 품질의 근거가 아니다. 현재 작업 우선순위는 `10`의 구조 감사와 `08`의 S단계다.

## 문서 책임 규칙

- 구현 순서와 상태는 `01`에만 기록한다.
- 코드 경계와 데이터 흐름은 `02`에만 기록한다.
- 서비스 추천 동작은 `03`에만 기록한다.
- 숫자와 조정 방법은 `04`에만 기록한다.
- 그래프 schema와 build 구조는 `05`에만 기록한다.
- 같은 설명을 여러 문서에 복제하지 않고 상대 링크로 연결한다.
- 현재 가중치는 확정값이 아니라 provisional 초기값이다.
- 기준선 test fixture와 실행 절차는 `06`에 기록하고, 구현 단위 검증 결과는 해당 구현 상태와 함께 기록한다.
- 전체 구조와 사용자 행동별 검토 사항은 `07`에 기록하고, 결정된 내용은 책임 문서 `01`~`05`로 옮긴다.
- 현재 기준점 이후의 통합 작업 목록과 우선순위는 `08`에 기록하고, 완료된 세부 결정은 책임 문서와 `01`의 상태에 반영한다.
- 구조적 문제를 다른 접근으로 전환한 판단 과정과 재검토 조건은 `09`에 기록한다. 단순 구현 누락이나 버그 목록은 넣지 않는다.
- V1/V2 대비 사용자 흐름과 운영 뼈대의 감사 결과, 확정 전 제안과 미검증 항목은 `10`에 기록한다. 확정된 계약만 `01`~`03`과 `08` 완료 조건으로 옮긴다.
- 장기·단기 추천의 반복 가능한 품질 기준점, 확인된 품질 문제와 고도화 순서는 `11`에 기록한다.
- 추천 품질 문제의 원인 가설, 검증 순서와 단계별 완료 조건은 `12`에 기록한다.
