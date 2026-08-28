# 06. V3 검증 기록

## 목적

이 문서는 완료된 합성 사용자 기준선 검증의 입력, 실행 범위와 결과를 기록한다. 새로운 테스트 계획이나 추천 품질 정답을 정의하지 않는다.

## Fixture

전체 fixture는 144명이다.

| 구분 | 인원 | 용도 |
| --- | ---: | --- |
| 학습 사용자 | 120 | stable, mixed, drift, negative 성향의 LightFM 학습과 known-user 후보 생성 |
| 학습 후 사용자 | 24 | model identity가 없는 cold/onboarding runtime 경로 검증 |

seed와 실행 도구는 `tests/v3_user_seed/`에 있다. 영화·온톨로지 데이터는 수정하지 않고 `v3seed-*` 사용자와 관련 행동·Redis key만 대상으로 한다.

단계별 seed를 사용한 이유는 다음과 같다.

1. 120명을 먼저 삽입해 dataset, model, top-150 candidate를 만든다.
2. model과 candidate를 고정한 뒤 24명을 추가해 feature-only cold 경로를 확인한다.
3. 학습 이후 known user에게 stable/drift 행동을 추가해 장기 모델을 바꾸지 않은 단기 적응을 확인한다.

## 검증한 흐름

```text
seed 삽입
-> dataset build
-> ontology item/user feature export
-> hybrid LightFM train + artifact reload
-> blockwise top-150 materialization + DB publish
-> serving bundle validation + activation
-> Redis blacklist/short-term 동기화
-> short-term worker
-> 실제 V3 adapter 요청
-> 응답, source, filter, score trace, latency 기록
```

검증 항목:

- model·ontology·feature·candidate·policy hash 호환성
- finite score와 embedding health
- watched/passed/adult/OTT/status hard filter
- 후보 중복과 DB 미존재 영화 노출
- known, cold, subscribed-only, onboarding mutation 경로
- short-term cache hit/miss와 worker 갱신
- 최대 100개 상세 처리와 top-150 예비 후보 보충
- invalid bundle 거부와 이전 정상 bundle 유지
- 기존 API 응답 schema

## 최종 결과

Phase A-F 응답 시간 검증 artifact:

- model: `hybrid-77a977915f6b-abb9c7b0706d-bf89dc0a3ba9-e0b8d8686041-a401dba670c5-7b869d3b`
- candidate snapshot: `cand-a439f89fd83762d09db1085e`
- policy: `v3-policy-quality-v1`
- bundle: `bundle-21b4407076b864c2940b9fa3`

최종 online 원본은 [v3_online_baseline_20260827T230722Z.json](diagnostics/v3_online_baseline_20260827T230722Z.json)이다.

| 구간 | 요청 | 평균 | p95 | 실패 |
| --- | ---: | ---: | ---: | ---: |
| known warm | 120 | 2.973초 | 3.430초 | 0 |
| cold warm | 24 | 1.390초 | 2.860초 | 0 |
| subscribed only | 24 | 2.155초 | 2.665초 | 0 |
| onboarding mutation | 6 | 2.657초 | 3.331초 | 0 |

- V3 단위 테스트 `122개`, 공용 추천 executor 테스트 `2개` 통과
- 128명 candidate materialization 성공
- top-150 `19,200건`, materialization `3.53초`, 사용자당 `0.027초`
- 제외 위반과 최종 중복 `0건`
- known 경로 short-term cache warm 상태 확인
- `SUBSCRIBED_ONLY`에서 구독 OTT 후보가 없는 한 건의 빈 결과는 정책상 정상

### Phase G 장기 재학습

post-model 행동을 포함한 현재 품질 artifact는 다음과 같다.

- model: `hybrid-589adbba344c-abb9c7b0706d-bf89dc0a3ba9-45932f2c79ee-9e3651b419af-7b869d3b`
- candidate snapshot: `cand-ca26023f3445afbcc294eb20`
- bundle: `bundle-31188df3ab847d4e31287cfc`
- 사용자 `128명`, positive pair `3,445개`, 후보 `19,200건`, 실패 `0건`

동일 stable 6명·drift 6명을 다시 평가한 결과 최근 drift 방향은 장기 top-20에 일부 반영됐지만 stable 품질과 후보 다양성이 하락했다. 상세 결과는 [Phase G 품질 결과](diagnostics/v3_quality_snapshot_20260828T042559Z.md)와 [Phase G 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260828T042637Z.md)에 있다.

### Phase H 연속 감쇠와 장기 ontology 후보

- model: `hybrid-02e666e23f10-d8dd44e869db-e2a5a2a2e0ca-45932f2c79ee-9e3651b419af-7b869d3b`
- candidate snapshot: `cand-dd6dd505d38733bfb53d2aa8`
- bundle: `bundle-ff3d35e49ba03cc72adc9eed`
- 사용자 `128명`, positive pair `3,445개`, 후보 `19,200건`, 실패 `0건`
- model health 통과, artifact reload exact match 통과

행동별 연속 half-life, 독립 장기 ontology 후보, model/ontology 의미 일치 기반 `45~65%` model 비중 제한을 적용했다. stable top-5 현재 장르 일치는 `30/30`, drift는 `22/30`이었고 제외 위반과 중복은 0건이었다. LightFM 장기 top-20 고유 영화는 45편으로 남았으나 최종 고유 영화는 Phase G의 108편에서 160편으로 증가했다. 상세 결과는 [Phase H 품질 결과](diagnostics/v3_quality_snapshot_20260828T050041Z.md)와 [Phase H 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260828T110011Z.md)에 있다.

## 병렬 처리 비교

2026-08-28에 ontology build worker 비교와 같은 방식으로 순차·2·4 worker를 왕복 순서로 두 번씩 측정했다. 모든 반복의 영화 ID와 순서 hash는 동일했다.

| 대상 | 선택 | 근거 |
| --- | --- | --- |
| 후보 100명 사전 계산 | 1 worker | 1/2/4 worker 중앙값이 2.667/2.838/2.758초로 병렬 이득 없음 |
| warm 추천 12건 | 2 worker | batch 32.424초에서 21.329초, 처리량 0.370에서 0.563 req/s로 개선 |
| 4 worker 요청 | 기본값 제외 | 처리량은 0.762 req/s지만 평균 요청이 5.166초, 최대 p95가 5.872초로 증가 |

요청 worker마다 DB session을 새로 열고 model artifact는 thread 간 공유한다. 원본은 [후보 병렬 비교](diagnostics/v3_candidate_parallel_benchmark_20260828T034644Z.md)와 [요청 병렬 비교](diagnostics/v3_request_parallel_benchmark_20260828T035841Z.md)다.

## 품질 검증 범위

Phase A~F는 12명의 post-model 사용자로 방향성을 확인했다.

- stable control 6명
- 실제 drift 6명
- 사용자당 최종 top-20
- 점수 자체보다 source 생존, 취향 방향, filter 위반과 ontology 근거를 확인

상세 결과는 [10 품질 개선 기록](10_quality_improvement_record.md)과 [진단 결과 목록](diagnostics/README.md)을 따른다.

## 증명하지 않은 것

- 실제 서비스 사용자의 relevance
- NDCG, Recall 또는 정답 기반 모델 우열
- 작은 합성 사용자 집단을 넘어선 협업 필터링 일반화
- 작은 합성 집단에서 발생한 장기 후보 과집중의 실제 사용자 일반화 여부
- 행동 중간 변경이 있는 같은 피드 세션의 완전한 연속 페이지 의미
- production scheduler의 재시작·중복 실행·부분 실패 복구
- production 규모의 지속 부하, queue 포화와 cache stampede

이 항목들은 테스트 실패가 아니라 현재 검증 범위 밖이며 [08 후속 작업](08_additional_work_backlog.md)에 기록한다.

## 보존과 정리

- 현재 품질 반복이 끝날 때까지 fixture와 활성 artifact를 유지한다.
- 정리 시 `v3seed-*` 사용자와 관련 Redis key만 제거한다.
- 영화, ontology build `22`, 실사용자 데이터는 제거하지 않는다.
- 같은 실험의 중간 진단은 최종 보고서가 확정되면 삭제한다.
