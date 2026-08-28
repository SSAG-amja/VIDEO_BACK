# V3 진단 결과

이 디렉터리에는 현재 설계 결정이나 회귀 기준을 뒷받침하는 최종 결과만 보존한다. 같은 실험의 실패·중간·교체된 결과는 최종 보고서 확정 후 제거한다.

## 권위 있는 결과

| 파일 | 용도 |
| --- | --- |
| `item_feature_export_build_22.json` | ontology build `22` full item feature export 규모와 메모리 진단 |
| `v3_cold_start_policy_v3_20260827.json` | 현재 cold-start 정책 비교 결과 |
| `v3_quality_snapshot_20260827T142248Z.{json,md}` | Phase A 수정 전 post-model 문제 기준점 |
| `v3_lightfm_ablation_20260827T172056Z.{json,md}` | Phase B 최종 LightFM 표현과 centering 선택 근거 |
| `v3_quality_snapshot_20260827T221657Z.{json,md}` | Phase C/D drift 판정과 short-only lane 결과 |
| `v3_ontology_ablation_20260827T222805Z.{json,md}` | Phase E ontology 0%/25% 비교 |
| `v3_catalog_negative_ablation_20260827T224355Z.{json,md}` | Phase F catalog soft 감점과 semantic negative 비교 |
| `v3_quality_snapshot_20260827T224522Z.{json,md}` | Phase B~F 통합 품질 결과 |
| `v3_online_baseline_20260827T230722Z.json` | Phase A-F bundle의 마지막 warm 응답 시간과 기능 기준선 |
| `v3_ontology_outlier_audit_20260827T233031Z.{json,md}` | stable 6명, drift 6명의 최종 이상치와 ontology 근거 감사 |
| `v3_candidate_parallel_benchmark_20260828T034644Z.{json,md}` | 후보 100명의 순차·2·4 worker 동적 큐 비교와 1 worker 선택 근거 |
| `v3_request_parallel_benchmark_20260828T035841Z.{json,md}` | warm 요청 12건의 순차·2·4 worker 비교와 2 worker 선택 근거 |
| `v3_quality_snapshot_20260828T042559Z.{json,md}` | drift 행동 포함 전체 재학습 후 stable 6명·drift 6명의 warm Phase G 품질 기준선 |
| `v3_ontology_outlier_audit_20260828T042637Z.{json,md}` | Phase G의 장기 후보 과집중과 과거 취향 잔존 감사 |
| `v3_quality_snapshot_20260828T050041Z.{json,md}` | 연속 감쇠·장기 ontology 독립 후보·의미 일치 제한을 적용한 Phase H 기준선 |
| `v3_ontology_outlier_audit_20260828T110011Z.{json,md}` | Phase H의 잔여 협업 과집중, 저투표 후보와 의미 근거 감사 |

## 보존 규칙

- JSON은 재분석 가능한 원본, Markdown은 사람이 읽는 요약이다.
- 최종 결과가 이전 결과를 완전히 대체하면 이전 파일을 제거한다.
- 정책 변경 전후를 설명하는 기준점은 현재 결정의 근거인 동안 보존한다.
- 진단 파일명에는 UTC 생성 시각을 포함한다.
- model binary, sparse matrix와 candidate artifact는 이 디렉터리에 두지 않는다.
- 실사용자 식별 정보나 비공개 원문 행동은 문서 artifact에 기록하지 않는다.
