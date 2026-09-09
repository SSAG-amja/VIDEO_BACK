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

- 기준일: `2026-09-10`
- ontology build: `22`
- model: `hybrid-0088e46c78e6-8ee76ae0cc79-51c5cf5ab1c1-32da0f11b507-16fe5319a5c2-7b869d3b`
- candidate snapshot: `cand-72e516bd88d5e91a4a67ae1a`
- policy: `v3-policy-quality-v1`
- serving bundle: `bundle-c43cac19fbffc33c399f75b9`
- V3 단위 테스트: `143개` 통과
- 공용 추천 executor 테스트: `2개` 통과
- 직전 응답 시간 기준선: [v3_online_baseline_20260827T230722Z.json](diagnostics/v3_online_baseline_20260827T230722Z.json)
- 현재 LightFM 기준선: [v3_lightfm_ablation_20260907T102400Z.md](diagnostics/v3_lightfm_ablation_20260907T102400Z.md)
- 현재 최종 품질 기준선: [v3_quality_snapshot_20260907T115937Z.md](diagnostics/v3_quality_snapshot_20260907T115937Z.md)
- 현재 이상치 감사: [v3_ontology_outlier_audit_20260907T120631Z.md](diagnostics/v3_ontology_outlier_audit_20260907T120631Z.md)

Phase A~F와 통합 bundle 검증은 완료됐다. 주요 반영 내용은 다음과 같다.

- LightFM feature block 정규화와 수치 health gate
- known-user 공통 인기 점수 50% centering
- `inactive`, `recent_interest`, `stable`, `drift` 단기 상태 분리
- drift 사용자에게만 15~40% short-only lane 적용
- ontology 25% 기여 유지와 LightFM 인과 설명 분리
- vote count 20 미만 catalog soft 감점
- exact passed/recent negative exclusion과 bounded semantic negative 유지
- top-150 저장 후 hard filter 탈락분만 예비 50개에서 보충, 상세 처리는 최대 100개
- 행동별 연속 half-life 감쇠와 장기 ontology 독립 후보
- 인기 영화·과다 행동 사용자 학습 기여 상한과 user identity/semantic 비율 완화
- 사용자 집단·입력·후보 다양성과 사용자별 근거량에 따른 동적 협업 신뢰도
- 협업 신뢰도에 따른 LightFM 내부 user-identity 성분 감쇠
- model/ontology 상위 후보 불일치 시 `65/35`, 일치율 상승 시 최대 `55/45`로 후보 선택
- 후보 선택용 ontology 점수와 최종 상세 ontology 점수를 분리해 최종 점수에는 한 번만 반영
- saved/pinned 공통 반감기 365일과 학습 당시 온보딩 전용 서명 기반 known-user 변경 감지

직전 다건 응답 시간 기준선은 known 평균 `2.973초`, p95 `3.430초`다. 현재 bundle 활성화 smoke test의 known-user 단일 요청은 `3.003초`였으며, 별도 부하 latency baseline은 다시 실행하지 않았다.

## 해석 제한

현재 결과는 합성 사용자 중심의 기능·방향성 기준선이다. 실사용자 relevance나 협업 필터링 품질을 확정하지 않는다.

- 현재 품질 감사 표본은 stable, mixed, drift, negative-heavy 각 6명으로 총 24명이다.
- post-model 행동 72건을 포함한 재학습과 연속 시간 감쇠 재학습은 완료됐다.
- 대표 24명의 LightFM top-20 사용자 간 Jaccard는 `21.2% → 9.8%`, 고유 영화는 `135 → 168편`으로 개선됐다.
- Phase I 원본 모델의 대표 24명 top-20은 480칸 중 168편, 사용자 간 Jaccard `9.8%`였다.
- identity-only 협업 감쇠를 반영한 대표 24명 저장 후보 top-20은 480칸 중 151편, 사용자 간 Jaccard `13.59%`였다.
- 120명 합성 집단의 협업 신뢰도 `0.1556`은 LightFM 전체 비중이 아니라 user-identity 성분에만 적용된다.
- 실제 추천 smoke test에서 model base/effective weight는 모두 `0.474`였고 최종 후보 100개를 정상 생성했다.
- 최종 480칸은 고유 310편, top-5 120칸은 고유 90편이었다.
- top-5 현재 취향 장르 일치는 stable `30/30`, drift `30/30`, negative-heavy `30/30`, mixed `29/30`이었다.
- 제외 위반과 사용자 내부 중복은 0건이고, 반복 top-5 영화가 현재 취향 장르 밖으로 퍼진 사례도 0건이었다.
- top-10 저투표 후보는 240칸 중 6건이며 mixed/drift의 ontology·short source에 집중됐다.
- 작은 합성 학습 집단에서 나타난 사용자 간 영화 반복은 실사용자 규모에서 다시 검증한다.
- NDCG와 Recall은 사용자가 명시적으로 현재 범위에서 제외했다.

## 남은 핵심 작업

즉시 점수 상수를 다시 조정하지 않는다. 후속 작업의 우선순위와 완료 조건은 [08 후속 작업](08_additional_work_backlog.md)에만 기록한다.

요청·후보 계산의 병렬 비교와 Phase I 협업 과집중 보정·통합 재검증은 완료됐다. 다음 품질 작업은 저신뢰 ontology/short 후보의 catalog trust이며, 그다음 bounded negative 충돌을 검토한다. 실제 사용자 규모의 협업 신뢰도 calibration은 별도 검증으로 남는다.

## 문서 규칙

- 현재 상태는 `README`, 남은 작업은 `08`, 품질 실험 결과는 `10`에서만 요약한다.
- 상세 숫자는 `04` 또는 `diagnostics/` 원본에 두고 다른 문서에는 결론만 반복한다.
- ontology 근거는 의미적 지지이며 LightFM 점수의 인과 설명으로 표현하지 않는다.
- 새 진단을 남길 때 같은 실험의 중간·실패 결과는 최종 결과가 확정되면 제거한다.
- binary model과 candidate artifact는 `assets/ml_models/v3/`에 두며 문서 디렉터리에 복제하지 않는다.
