# 08. V3 후속 작업

## 목적

현재 활성 V3 기준점 이후에 남은 작업만 관리한다. 완료된 구현 계획은 제거하고, 완료 결과는 [01 아키텍처와 파이프라인](01_architecture_and_pipeline.md)과 [10 품질 개선 기록](10_quality_improvement_record.md)에 둔다.

우선순위는 다음 순서를 따른다.

1. 추천 흐름의 정확성과 누락된 서비스 계약
2. 정책상 미구현·미연결 항목
3. 추천 품질 고도화
4. 트래픽 처리와 최적화
5. 사용자가 명시적으로 미룬 기능

## 현재 완료 기준점

- S0~S9 구현 완료
- graph build `22`와 item feature export 완료
- Phase A~F 품질 수정과 통합 bundle 활성화 완료
- top-150 저장, hard filter 탈락분 예비 50개 보충, 최대 100개 상세 처리 완료
- short-term 24시간 누적, threshold, debounce, lease, cache format 3 완료
- stable/drift 판정과 drift short-only lane 완료
- LightFM 수치 health gate, score centering, ontology ablation, catalog/negative 정책 검증 완료
- 최종 V3 단위 테스트 `142개`, 공용 추천 executor 테스트 `2개` 통과
- 요청 bounded executor와 후보 user-block 동적 큐 비교·적용 완료

현재 artifact와 응답 시간은 [README](README.md)를 기준으로 한다.

## P0. 추천 흐름과 운영 정확성

### P0-01. V3 정기 재학습과 bundle 게시

- 상태: `미구현`
- 문제: short-term worker는 있지만 dataset snapshot, LightFM 재학습, top-150 생성, 검증, bundle 게시를 정기 실행하는 V3 scheduler가 없다.
- 구현: stage별 idempotency, 전체 pipeline lock, retry, checkpoint, 부분 실패 시 이전 bundle 유지가 필요하다.
- 완료 조건: 새 행동이 정해진 주기에 장기 모델까지 반영되고 불완전한 조합은 활성화되지 않는다.

### P0-02. 행동 commit 이후 단기 작업 전달 보장

- 상태: `미구현`
- 문제: DB 행동 저장 뒤 Redis 예약이 실패하면 short-term 선계산 작업이 유실될 수 있다. 다음 요청은 DB fallback으로 현재 filter/profile을 복구하지만 독립 단기 후보 선계산까지 보장하지 않는다.
- 후보: transactional outbox 또는 DB pending marker와 재처리 worker.
- 완료 조건: Redis 장애 후에도 DB에 확정된 갱신 대상이 결국 한 번 이상 처리되고 중복 처리가 결과를 손상하지 않는다.

### P0-03. 피드 세션과 연속 페이지

- 상태: `미구현, 서비스 의미 결정 필요`
- 문제: `shuffle_seed`는 진단 키로만 사용되고 실제 순서를 고정하지 않는다. 요청마다 최대 100개를 다시 계산한 뒤 offset으로 자르므로 페이지 사이 행동·상태 변경 시 중복 또는 누락이 생길 수 있다. session 노출 입력도 비어 있다.
- 결정할 내용: 같은 세션의 순위 고정 범위, Pass/Watch 제거 자리 보충, 동일 페이지 재요청, session TTL, bundle 변경 시 폐기.
- 완료 조건: 첫 20개부터 최대 100개까지 행동이 끼어도 정의된 중복·누락 정책을 지키고 `has_more` 의미가 일치한다.

### P0-04. 새로고침과 후보 100개 소진

- 상태: `명시적 후속 결정`
- 현재 결정: top-150 중 예비 50개는 hard filter 탈락분만 채우고 나머지는 버린다.
- 미결정: 100개를 모두 본 뒤와 새로고침 시 새 후보를 언제 다시 계산할지.
- 주의: watched/passed 해제로 저장 후보에 없던 영화가 복구되는 시점도 이 정책과 함께 정한다.

### P0-05. 영화 metadata 자동 반영

- 상태: `미구현`
- 문제: 장르·배우·감독·keyword·overview가 바뀌면 graph와 feature/model 호환성이 달라지지만 현재 updater가 graph/model/bundle 갱신을 자동 실행하지 않는다.
- 구현: 변경 유형 분류, 영향 영화 추적, 증분 또는 전체 graph build 판정, item export, 필요 시 model/candidate 재생성, 검증된 bundle 게시.
- 완료 조건: 변경 종류별 최대 반영 지연과 실패 시 이전 bundle 유지가 정의된다.

### P0-06. 화면 fallback의 V3 장애 관측

- 상태: `미구현`
- 문제: 화면 API는 V3 실패 시 기존 추천이나 인기 결과를 반환할 수 있어 장애가 성공 응답에 가려질 수 있다.
- 완료 조건: V3 실패율, fallback source와 지속 시간을 metric/log/alert로 확인할 수 있다.

### P0-07. 단기 정책 변경과 cache 호환성

- 상태: `보류`
- 문제: 기간, 최대 행동 수, 시간 감쇠, threshold는 수정 가능한 정책이지만 변경된 계산 기준과 기존 cache가 섞이지 않아야 한다.
- 재개 조건: 단기 수치를 실제로 조정할 때 policy snapshot/hash와 cache namespace 호환성을 함께 구현한다.

## P1. 정책상 미구현·미연결

### P1-01. 소셜 행동 학습 연결

- 상태: `미구현`
- 현재: 게시글·좋아요·댓글 projector와 provenance 진단은 있으나 LightFM training eligibility는 꺼져 있다.
- 이유: 게시글과 댓글은 영화에 대한 긍정·부정 방향이 명확하지 않다. `likes.created_at`도 없어 historical cutoff에 직접 사용할 수 없다.
- 후속: 명시적 대상 영화, 중립 engagement cap, event-time playlist 구성 가능 범위를 먼저 정한다.

### P1-02. Immutable 행동 event

- 상태: `미구현`
- 문제: 현재 dataset은 mutable saved/pinned/watched/passed/favorite 상태를 읽으므로 반복 행동, 해제·재행동과 과거 playlist 구성을 정확히 재현하지 못한다.
- 완료 조건: 임의 cutoff의 학습 dataset을 재현하고 current snapshot과 historical build를 구분한다.

### P1-03. 개별 차단 영화 입력

- 상태: `결정 필요`
- 현재 policy 필드는 있지만 실제 요청 입력은 비어 있다.
- 결정: 별도 차단 기능을 연결하거나 미사용 계약을 제거한다.

### P1-04. 상세 evidence path

- 상태: `미구현`
- 현재는 관계 유형과 점수까지만 기록한다.
- 후속: 어떤 행동, 어떤 concept, 어떤 graph edge를 거쳤는지 build와 함께 재현하되 LightFM의 인과 설명처럼 표현하지 않는다.

## P2. 추천 품질 후속

### P2-01. 새 취향의 장기 모델 반영 검증

- 상태: `검증 완료, 품질 문제 확정`
- 실행 결과: post-model 저장 행동 72건을 포함해 positive pair가 `3,373 → 3,445`로 증가했고 동일 Phase B 설정으로 model, top-150, bundle을 재생성했다.
- 개선 신호: drift 장기 top-20의 최근 장르 포함률은 `26.7% → 40.0%`, 과거 장르 포함률은 `81.7% → 60.0%`로 이동했다.
- 회귀 신호: stable 장기 장르 포함률은 `89.2% → 60.8%`, 장기 고유 영화는 `86 → 45편`, drift 최종 top-5 현재 장르 일치는 `18/30 → 16/30`으로 하락했다.
- 판정: 최근 행동은 학습됐지만 모델이 공통 인기 영화 방향으로 과집중했다. 단기 후보 누락이나 단순 lane 비율 문제가 아니라 LightFM 학습 데이터·feature 표현·협업 일반화 문제를 다음 품질 작업에서 다룬다.

### P2-02. 실제 사용자 규모의 협업 효과

- 상태: `합성 기준선 보정 완료, 실사용자 검증 대기`
- 완료: 코호트 독점·사용자별 회전 시드, 인기 영화 sample 감점, 사용자 총기여 상한, identity/semantic `2.0/0.5`, identity-only 동적 협업 신뢰도, 강화된 집중도 health gate를 적용했다.
- 결과: 원본 모델의 대표 24명 LightFM top-20 Jaccard `21.2% → 9.8%`, 고유 영화 `135 → 168`, stable 장르 overlap `85.8%` 유지. identity-only 감쇠 저장 후보는 고유 영화 151편, Jaccard `13.59%`였다. 현재 120명 population confidence는 `0.1556`이다.
- 남은 작업: 실제 사용자 cutoff와 random seed를 고정하고 사용자 규모·취향 분포별 confidence calibration을 다시 검증한다. 특히 초기에 한 취향이 몰린 경우에도 population confidence가 낮게 유지되는지 관측한다.
- 주의: 합성 결과만으로 협업 신뢰도 임계값을 확정하지 않는다.

### P2-02A. Corrected bundle 통합 품질 재검증

- 상태: `완료, 2026-09-07`
- 이유: 전체 LightFM lane을 협업 신뢰도로 줄인 이전 Phase I 최종 보고서는 잘못된 구조를 측정했으므로 폐기했다.
- 결과: model base/effective weight가 모든 유형에서 같았고 최종 480칸 중 고유 310편, top-5 120칸 중 고유 90편이었다. stable·drift-current·negative-heavy top-5는 `30/30`, mixed는 `29/30`이 현재 목표 장르와 일치했다.
- 불변식: 제외 위반, 사용자 내부 중복, 반복 top-5 영화의 현재 장르 불일치는 모두 0건이었다.
- 남은 문제: top-10 저투표 6건, 과다 장르 metadata 11건, 높은 부정 근거 충돌 9건이 남았다.

### P2-02B. 장기 후보의 catalog trust와 필터 역할 분리

- 상태: `장기 4개 사용자 유형 완료, short-term 검증 대기`
- 확인 결과: corrected 결과의 top-10 저투표 후보는 240칸 중 6건이며 mixed/drift의 ontology·short source에 집중됐다. 전체 top-20 기준 저투표 비율은 stable `1.7%`, mixed `19.2%`, drift `12.5%`, negative-heavy `2.5%`다.
- 문제: 의미 일치가 강한 저투표 영화와 장르 8개 이상 metadata 영화가 catalog soft 감점 뒤에도 남는다. 일부는 drift short-only lane에서 강제 선택된다.
- 적용: field별 상한과 keyword IDF가 들어간 artifact 행렬을 장기 ontology 후보 생성과 상세 분석에서 공통 사용한다. 후보 목록 불일치 시 model/ontology 비율을 `0.65/0.35`로 두고, 후보 선택 ontology 점수를 최종 personal 성분에서 제거했다. 초기 catalog 자격 필터는 DB/제목/adult/차단 상태와 장기 mature cold-item 신뢰를 후보 Top-K 전에 처리한다. 요청 최종 필터는 watched/passed, blacklist, 세션과 OTT를 처리하고 예비 50개로 보충한다.
- 장기 교차 검증: `dense_focused`, `dense_diverse`, `light_focused`, `light_diverse` 각 1명에서 최종 후보 100개와 holdout positive 오차단 0건을 확인했다. Recall@100은 모두 유지됐고 NDCG@100 변화는 `0`, `-0.0021`, `0`, `+0.0011`이었다. 기존 17개만 남았던 `light_focused` 사용자도 100개로 복구됐다.
- 실제 사전 계산 검증: 비활성 모델 전체 catalog 1,176,540편 중 초기 자격 54,789편을 남겼다. 사용자 240명 모두 top-150을 채워 총 36,000개를 생성했고 실패는 0명, 후보 점수 계산은 7.27초였다. 검증 snapshot `cand-60190ab9647bf37f38ed2177`은 게시하거나 활성화하지 않았다.
- 남은 작업: short-term 후보에도 같은 신뢰도 원칙이 필요한지 별도로 확인한다. source별 vote 분포를 고정 표본에서 비교하되 무조건적인 인기 영화 우대나 전체 long-tail 제거로 해결하지 않는다.
- 완료 조건: stable/drift top-5 방향과 현재 고유 영화 수준을 유지하면서 저투표·과다 장르 후보를 낮춘다.

### P2-03. Source별 calibration과 새 reranker

- 상태: `후속 고도화`
- 배경: short-only 후보는 model 후보보다 ontology 의존도가 높다. 현재 lane과 percentile 정규화는 동작하지만 향후 reranker 교체 시 source별 점수 분포를 다시 맞춰야 한다.
- 완료 조건: model-only, model+short, short-only, cold ontology source를 같은 후보·filter 조건에서 비교한다.

### P2-04. Cold-start 유사 사용자 후보

- 상태: `사용자 증가 후 검토`
- 배경: 장르·선호 영화가 적은 사용자는 rule/ontology만으로 취향을 좁게 해석할 수 있다.
- 후속: 비슷한 온보딩과 충분한 행동 지지를 가진 사용자 후보를 별도 source로 추가하고 최소 사용자 수와 fallback 기준을 둔다.

### P2-05. Catalog metadata 이상치

- 상태: `후속 데이터 품질`
- 배경: 다수 장르가 잘못 붙은 영화는 ontology 일치를 과대 생성할 수 있다. 현재 vote-count soft 감점은 신뢰도를 낮추지만 잘못된 metadata 자체를 고치지 않는다.
- 후속: 비정상 장르 수·성인/상태·overview 품질 진단과 원천 정정 절차를 만든다.

### P2-06. 추천 결과 반복 분석

- 상태: `지속 작업`
- 방법: 평가지표를 먼저 늘리지 않고 사용자 유형, 후보 source, 장기·단기 방향, filter, ontology 근거와 이상치를 함께 확인한다.
- 기준 원본: [최종 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260827T233031Z.md).

## P3. 트래픽 처리와 최적화

기본 2 worker 요청 실행과 비교 기준선까지만 적용했다. 추가 성능 최적화와 대규모 동시 요청 대응은 사용자가 명시적으로 마지막 단계로 미뤘다.

### P3-00. 병렬 처리와 동적 스케줄링

- 상태: `기준선 완료, 2026-08-28`
- 요청 경로: 공용 `ThreadPoolExecutor`가 완료된 worker에 다음 요청을 전달한다. `/shorts`와 `/movies/recommended`의 동기 추천 계산은 API event loop 밖에서 실행하며 작업마다 독립 `SessionLocal`을 사용한다.
- 기본값: `RECOMMENDATION_EXECUTOR_WORKERS=2`. 4 worker는 환경 변수로 선택할 수 있지만 현재 기준선에서는 개별 요청 지연 증가가 커 기본값으로 쓰지 않는다.
- 후보 사전 계산: user block 공유 동적 큐와 `--workers`를 구현했다. 워커 수는 실행 설정으로만 기록하며 동일 후보 스냅샷의 의미 hash를 바꾸지 않는다.
- 후보 기본값: 1 worker. block 내부 BLAS 벡터 연산이 이미 병렬 계산 자원을 사용해 Python worker를 늘려도 처리량이 개선되지 않았다.

| 구간 | 1 worker | 2 worker | 4 worker | 결정 |
| --- | ---: | ---: | ---: | --- |
| 후보 100명 batch | 2.667초 | 2.838초 | 2.758초 | 1 worker 유지 |
| warm 요청 12명 batch | 32.424초 | 21.329초 | 15.751초 | 2 worker 기본 |
| warm 요청 처리량 | 0.370 req/s | 0.563 req/s | 0.762 req/s | 2 worker는 1 worker 대비 약 52% 증가 |
| warm 요청 평균 | 2.702초 | 3.551초 | 5.166초 | 4 worker의 개별 지연 증가로 제외 |
| warm 요청 최대 p95 | 3.273초 | 3.998초 | 5.872초 | 2 worker 선택 |

### P3-01. 장기 ontology 후보 cache 또는 사전 계산

- 상태: `field-budgeted artifact 경로 개선, cache·사전 계산은 보류`
- 문제: Phase H의 장기 ontology 후보는 bounded set query지만 known 요청마다 실행한다. 12명 순차 품질 진단에서 기존 경로보다 유의미한 추가 지연이 확인됐다.
- 현재: field-budgeted artifact에서는 graph 전체 통계를 요청마다 계산하지 않고 sparse item feature 행렬을 blockwise 조회한다. 사용자 `44652`의 장기 ontology 후보 100개는 `0.298초`였다. 구형 artifact의 DB fallback과 profile 변경 cache 정책은 그대로 남아 있다.
- 원칙: 품질 source를 제거하지 않고 장기 profile signature와 ontology build에 묶인 cache 또는 행동 변경 기반 사전 계산으로 옮긴다. DB fallback과 source별 score trace는 유지한다.
- 검증: cache hit/miss 결과 순서가 같아야 하며, profile 변경 후 stale 후보 사용 범위와 TTL을 명시한다. 현재 bundle의 정확한 단일 요청 latency는 성능 작업 재개 시 다시 측정한다.

모든 1·2·4 worker 반복에서 영화 ID와 순서 hash가 같았고 peak RSS는 후보 약 967MiB, 요청 약 938MiB로 worker별 유의한 model 복제가 없었다. 원본은 [후보 비교](diagnostics/v3_candidate_parallel_benchmark_20260828T034644Z.md)와 [요청 비교](diagnostics/v3_request_parallel_benchmark_20260828T035841Z.md)다.

| 항목 | 현재 문제 | 후보 접근 |
| --- | --- | --- |
| known ontology 상세 분석 | warm 평균 약 3초의 주요 요청 비용 | 상위 후보 evidence 선계산, set-based 집계 축소 |
| cold/onboarding graph 집계 | cache 없이 요청 시 계산 | token별 bounded index, 변경 시 비동기 선계산 |
| cache miss 단기 역조회 | 요청이 단기 후보 생성 비용 부담 | 만료 전 선제 갱신, stale-while-refresh |
| process 첫 model load | 큰 artifact 첫 요청 비용 | startup preload와 readiness gate |
| 동시 요청 | bounded executor는 완료, 같은 사용자 계산은 중복 가능 | single-flight, 대기 queue 상한, timeout 정책 |
| 진단 저장 | 요청마다 DB 저장 비용 | sampling, 비동기 기록, retention |
| graph build | build `22` 498.3초 | 증분 build, stage 병렬성·bulk path 유지 |
| artifact/fixture | 반복 실험 파일 증가 | active/previous/diagnostic 보존 정책과 안전 cleanup |

성능 변경은 before/after p50, p95, max, query 수와 결과 불변식을 함께 기록한다.

## P4. 사용자가 명시적으로 미룬 범위

다음은 누락이 아니라 현재 구현하지 않기로 한 항목이다. 위 작업이 끝나고 사용자가 재개를 결정할 때만 진행한다.

- random 후보 혼합
- 신작 강제 혼합
- long-tail 또는 낮은 노출량 탐색
- 추가 다양성 quota
- 게시글 본문·hashtag 감정 및 의미 NLP
- NDCG·Recall 정량 평가

이미 구현된 결정적 반복 감점과 MMR은 보류 대상이 아니다.

## 다음 재개점

P3-00, P2-01과 Phase I의 학습·후보 적용 경계 수정·통합 품질 재검증은 완료됐다. 사용자의 현재 우선순위에 따라 운영·성능·부가 정책은 마지막으로 미룬다. 다음 작업은 P2-02B ontology·short 후보의 catalog trust이며, 이후 bounded negative 충돌과 실제 사용자 규모의 협업 calibration을 판단한다.
