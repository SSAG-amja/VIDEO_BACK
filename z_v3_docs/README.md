# V3 추천 시스템 문서

이 디렉터리는 V3 추천 엔진의 현재 설계, 구현 결과, 검증 근거와 후속 작업을 관리한다. 과거 계획보다 현재 코드와 활성 artifact를 우선하며, 완료된 작업은 실행 결과만 남긴다.

## 문서 지도

| 문서 | 책임 |
| --- | --- |
| [01 아키텍처와 파이프라인](01_architecture_and_pipeline.md) | 계층 구조, 학습·요청·행동 파이프라인과 S0~S9 구현 결과 |
| [02 구현 방식](02_implementation_guide.md) | package, dataset, model, candidate, bundle의 코드·데이터 경계 |
| [03 추천 정책](03_recommendation_policy.md) | 행동 의미, 후보 source, filter, 점수, 단기 취향, cold-start 정책 |
| [04 LightFM 조정 지점](04_lightfm_tuning.md) | 학습 weight, hyperparameter, score calibration과 현재 기준값 |
| [05 온톨로지 구조](05_ontology_structure.md) | graph schema, evidence, feature export, build 구조 |
| [06 검증 기록](06_validation_record.md) | seed 144명으로 수행한 기능·응답 시간 검증과 범위 |
| [07 전체 추천 흐름](07_end_to_end_flow_review.md) | 사용자 행동부터 장기·단기 후보와 최종 응답까지의 흐름 |
| [08 후속 작업](08_additional_work_backlog.md) | 미구현, 검증 대기, 품질 후속, 최적화, 명시적 보류 항목 |
| [09 설계 판단 기록](09_design_decision_journal.md) | 문제를 다른 구조로 해결한 이유와 재검토 조건 |
| [10 품질 개선 기록](10_quality_improvement_record.md) | Phase A~F에서 확인하고 반영한 내용과 남은 품질 판단 |
| [진단 결과 목록](diagnostics/README.md) | 보존 중인 권위 있는 진단 파일과 용도 |

처음 인수인계받는 경우 다음 순서로 읽는다.

```text
README -> 01 -> 07 -> 08
                  -> 품질 작업: 10 -> 04 -> 03
                  -> graph 작업: 05 -> 02
```

## 현재 기준점

- 기준일: `2026-08-28`
- ontology build: `22`
- model: `hybrid-02e666e23f10-d8dd44e869db-e2a5a2a2e0ca-45932f2c79ee-9e3651b419af-7b869d3b`
- candidate snapshot: `cand-dd6dd505d38733bfb53d2aa8`
- policy: `v3-policy-quality-v1`
- serving bundle: `bundle-ff3d35e49ba03cc72adc9eed`
- V3 단위 테스트: `122개` 통과
- 공용 추천 executor 테스트: `2개` 통과
- 직전 응답 시간 기준선: [v3_online_baseline_20260827T230722Z.json](diagnostics/v3_online_baseline_20260827T230722Z.json)
- 현재 품질 기준선: [v3_quality_snapshot_20260828T050041Z.md](diagnostics/v3_quality_snapshot_20260828T050041Z.md)
- 현재 이상치 감사: [v3_ontology_outlier_audit_20260828T110011Z.md](diagnostics/v3_ontology_outlier_audit_20260828T110011Z.md)

Phase A~F와 통합 bundle 검증은 완료됐다. 주요 반영 내용은 다음과 같다.

- LightFM feature block 정규화와 수치 health gate
- known-user 공통 인기 점수 90% centering
- `inactive`, `recent_interest`, `stable`, `drift` 단기 상태 분리
- drift 사용자에게만 15~40% short-only lane 적용
- ontology 25% 기여 유지와 LightFM 인과 설명 분리
- vote count 20 미만 catalog soft 감점
- exact passed/recent negative exclusion과 bounded semantic negative 유지
- top-150 저장 후 hard filter 탈락분만 예비 50개에서 보충, 상세 처리는 최대 100개
- 행동별 연속 half-life 감쇠와 장기 ontology 독립 후보
- model/ontology 상위 후보 일치율에 따른 LightFM 비중 `45~65%` 제한

직전 응답 시간 기준선은 known 평균 `2.973초`, p95 `3.430초`다. 성능 검증은 보류했으므로 현재 Phase G bundle 전체에 대한 latency baseline은 다시 실행하지 않았다.

## 해석 제한

현재 결과는 합성 사용자 중심의 기능·방향성 기준선이다. 실사용자 relevance나 협업 필터링 품질을 확정하지 않는다.

- 품질 감사 표본은 stable 6명, drift 6명이다.
- post-model 행동 72건을 포함한 재학습과 연속 시간 감쇠 재학습은 완료됐다.
- LightFM 장기 top-20 고유 영화는 여전히 `45편`이지만 독립 ontology 병합 후 최종 고유 영화는 `108 → 160편`으로 늘었다.
- drift top-5 현재 장르 일치는 `16/30 → 22/30`, 반복 규칙은 `25 → 13건`으로 개선됐다.
- 저투표 top-10 이상치는 `1 → 8건`으로 늘어 source별 catalog trust가 다음 품질 문제다.
- 작은 합성 학습 집단에서 나타난 사용자 간 영화 반복은 실사용자 규모에서 다시 검증한다.
- NDCG와 Recall은 사용자가 명시적으로 현재 범위에서 제외했다.

## 남은 핵심 작업

즉시 점수 상수를 다시 조정하지 않는다. 후속 작업의 우선순위와 완료 조건은 [08 후속 작업](08_additional_work_backlog.md)에만 기록한다.

요청·후보 계산의 병렬 비교와 Phase H 품질 검증은 완료됐다. 운영·성능·부가 정책은 보류하고, 다음 작업은 저신뢰 ontology/short 후보의 catalog trust와 남은 LightFM 과집중을 분리해 개선하는 것이다.

## 문서 규칙

- 현재 상태는 `README`, 남은 작업은 `08`, 품질 실험 결과는 `10`에서만 요약한다.
- 상세 숫자는 `04` 또는 `diagnostics/` 원본에 두고 다른 문서에는 결론만 반복한다.
- ontology 근거는 의미적 지지이며 LightFM 점수의 인과 설명으로 표현하지 않는다.
- 새 진단을 남길 때 같은 실험의 중간·실패 결과는 최종 결과가 확정되면 제거한다.
- binary model과 candidate artifact는 `assets/ml_models/v3/`에 두며 문서 디렉터리에 복제하지 않는다.
