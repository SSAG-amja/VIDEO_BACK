# 10. V3 추천 품질 개선 기록

## 목적

이 문서는 완료된 Phase A~F의 문제, 조치, 검증 결과와 남은 해석 제한을 기록한다. 이후 작업 계획은 [08 후속 작업](08_additional_work_backlog.md)에만 둔다.

## 시작 시 확인한 문제

초기 post-model 분석에서는 다음 현상이 확인됐다.

1. LightFM embedding과 raw score가 발산해 장기 후보를 신뢰하기 어려웠다.
2. stable과 drift 사용자의 변화 신뢰도가 거의 구분되지 않았다.
3. 단기 후보 생성기는 새 취향 영화를 찾았지만 최종 top-20에서 거의 모두 탈락했다.
4. 후보 source별 score scale과 ontology 기여를 분리해 검증할 수 없었다.
5. 저투표 catalog와 semantic negative 정책의 실제 효과가 확인되지 않았다.

초기 기준 원본은 [Phase A post-model 결과](diagnostics/v3_quality_snapshot_20260827T142248Z.md)다.

| 항목 | stable 6명 | drift 6명 |
| --- | ---: | ---: |
| 기존 drift confidence | 0.701 | 0.737 |
| 단기 후보와 단기 장르 | 0.857 | 0.848 |
| 최종 결과와 단기 장르 | 0.338 | 0.165 |
| 최종 top-20 단기 source | 0.8개 | 0개 |
| 장기 raw score 최대 | 약 `4.0e12` | 약 `7.1e12` |

단기 문제의 주 원인은 후보 생성 실패가 아니라 변화 판정, 후보 병합과 최종 생존 구조였다.

## Phase A. 평가 입력 분리

완료 내용:

- 학습 당시 known user 12명을 stable control 6명과 실제 drift 6명으로 분리했다.
- LightFM model과 장기 candidate를 고정한 뒤 최근 saved 행동을 추가했다.
- 후보 단계별 model-only, short-only, overlap과 최종 생존을 기록했다.
- 품질 실험은 정답 지표가 아니라 후보 source와 취향 방향을 보는 간이 검증으로 한정했다.

이 구조는 “단기 행동이 후보를 만들지 못하는가”와 “만든 후보가 최종 순위에서 탈락하는가”를 분리했다.

## Phase B. LightFM 수치 안정화와 장기 후보 보정

최종 모델:

```text
hybrid-77a977915f6b-abb9c7b0706d-bf89dc0a3ba9-e0b8d8686041-a401dba670c5-7b869d3b
```

반영 내용:

- semantic feature block L1 정규화
- positive interaction 근거가 있는 영화만 identity feature 사용
- user identity/semantic `4.0/0.25`, item identity/semantic `1.0/1.0`
- components `32`, epochs `20`, learning rate `0.01`, alpha `1e-5`
- embedding, norm, prediction, 후보 집중도 health gate
- known-user 공통 평균 item score의 90%를 제거하는 centering

```text
centered_score(u, i)
= raw_score(u, i) - 0.9 * mean_known_user_score(i)
```

대표 사용자 24명 결과:

| 항목 | centering 전 | 최종 0.9 |
| --- | ---: | ---: |
| top-100 고유 영화 | 135 | 433 |
| top-100 사용자 Jaccard | 0.789 | 0.338 |
| top-20 고유 영화 | 26 | 135 |
| top-20 사용자 Jaccard | 0.806 | 0.212 |
| stable top-20 장르 overlap | 0.508 | 0.858 |

최종 embedding 최대 절댓값은 user `0.294`, item `0.284`, 표본 prediction 최대 절댓값은 `4.627`이었다. 자세한 비교는 [LightFM ablation](diagnostics/v3_lightfm_ablation_20260827T172056Z.md)에 있다.

채택하지 않은 시도:

- metadata-only: 사용자별 후보가 같고 저투표 영화가 과다해 제외
- item-frequency 역제곱근 sample 보정
- item bias 제거
- epochs `80`

이 시도들은 후보 집중도를 의미 있게 개선하지 못했다.

## Phase C. 단기 취향 상태 재설계

최근 행동량과 실제 의미 변화를 분리해 네 상태를 정의했다.

| 상태 | 의미 |
| --- | --- |
| `inactive` | 최근 positive 근거가 갱신 기준 미만 |
| `recent_interest` | 최근 근거는 충분하지만 비교 가능한 장기 근거가 부족 |
| `stable` | 장기와 최근 의미 거리가 임계값 미만 |
| `drift` | 장기와 최근 의미 거리가 임계값 이상 |

최근 근거는 서로 다른 positive 영화 3편, 또는 서로 다른 2편이면서 행동 가중치 합 2.0 이상이다. 같은 영화의 여러 positive 상태는 최대 행동 가중치만 사용한다.

의미 거리는 genre/theme/mood 주요축과 keyword/actor/director 보조축의 정규화된 overlap으로 계산한다. 보조축만 비교할 때 거리를 제한하고 자연스러운 차이 0.35 이하는 잡음으로 제거한다. drift 임계값은 0.70이다.

| 항목 | stable control | 실제 drift |
| --- | ---: | ---: |
| 상태 판정 | stable 6/6 | drift 6/6 |
| 의미 거리 평균 | 0.582 | 0.876 |
| confidence 평균 | 0.356 | 0.810 |
| confidence 범위 | 0.196~0.459 | 0.668~0.898 |

## Phase D. 단기 후보 최종 생존

단순 점수 가중합만으로 short-only 후보가 사라지는 문제를 drift 전용 lane으로 해결했다.

- stable과 recent-interest에는 강제 lane을 적용하지 않는다.
- drift lane 비율은 `0.15 + 0.25 * drift_confidence`, 최소 15%, 최대 40%다.
- hard filter를 먼저 적용하고 실제 통과한 short-only 후보만 사용한다.
- 최대 100개 전체 순서를 결정적으로 만든 뒤 lane을 분산 배치한다.
- `model+short` 후보는 두 근거를 보존하지만 short-only quota를 소비하지 않는다.
- trace에 `short_term_lane_forced`와 단계별 후보 수를 기록한다.

| 항목 | stable control | 실제 drift |
| --- | ---: | ---: |
| 최종 top-20 short-only 평균 | 0.00 | 6.83 |
| 최종 top-20 short-only 비율 | 0% | 34.2% |
| 최종 결과의 단기 장르 일치 | 0.303 | 0.398 |

제외 위반과 최종 중복은 0건이었다. 중간 결과는 [Phase C/D 결과](diagnostics/v3_quality_snapshot_20260827T221657Z.md)에 있다.

## Phase E. Ontology 기여도 검증

같은 retrieval 후보와 hard filter/lane 결과에서 ontology component만 `0%`와 `25%`로 비교했다.

| profile | 장기 장르 일치 0%→25% | 단기 장르 일치 0%→25% | short-only 비율 |
| --- | ---: | ---: | ---: |
| stable | 0.302→0.409 | 0.214→0.303 | 0% 유지 |
| drift | 0.271→0.345 | 0.372→0.398 | 34.2% 유지 |

관계군 기여는 genre 55.1%, mood 20.8%, theme 15.6%, keyword 7.0%, director 0.9%, actor 0.5%였다. theme/mood 근거가 있는 영화는 장르만 일치한 영화보다 평균 순위가 개선됐다.

결정:

- personal/ontology `0.75/0.25` 유지
- short-term ontology multiplier `0.5` 유지
- graph build `22` 재빌드 없음
- 추가 IDF나 family별 cap은 현재 넣지 않음

Ontology 근거는 추천의 의미적 지지이며 LightFM이 해당 관계 때문에 점수를 냈다는 인과 설명이 아니다. 자세한 결과는 [ontology ablation](diagnostics/v3_ontology_ablation_20260827T222805Z.md)에 있다.

## Phase F. Catalog 신뢰도와 negative

저투표 일반 후보에는 hard filter 대신 다음 soft 감점을 적용했다.

```text
vote_count >= 20: 0
vote_count < 20: 0.05 * (20 - vote_count) / 20
```

이 감점은 장·단기 장르 방향을 유지하면서 drift의 vote 0 비율을 1.7%에서 0.8%, negative-heavy의 vote 0 비율을 0.8%에서 0%로 낮췄다.

Semantic negative 감점은 비활성화 대비 선택 결과의 weighted negative evidence를 낮췄다.

| 유형 | 감점 비활성 | 현재 감점 | 감소율 |
| --- | ---: | ---: | ---: |
| stable | 0.660 | 0.519 | 21.4% |
| drift | 1.323 | 1.069 | 19.2% |
| negative-heavy | 2.199 | 1.662 | 24.4% |

exact passed/recent negative hard exclusion 위반은 0건이었다. 기존 semantic negative 상한인 base의 30%와 절대 0.20 중 작은 값을 유지한다. 원본은 [catalog/negative ablation](diagnostics/v3_catalog_negative_ablation_20260827T224355Z.md)이다.

## Phase A-F 통합 결과

- model: `hybrid-77a977915f6b-abb9c7b0706d-bf89dc0a3ba9-e0b8d8686041-a401dba670c5-7b869d3b`
- candidate snapshot: `cand-a439f89fd83762d09db1085e`
- policy: `v3-policy-quality-v1`
- bundle: `bundle-21b4407076b864c2940b9fa3`

128명 candidate materialization은 전원 성공했고 top-150 `19,200건`을 `3.53초`, 사용자당 `0.027초`에 생성했다. 통합 post-model 결과에서 stable 6명은 모두 stable, drift 6명은 모두 drift로 분류됐고 drift top-20에는 평균 6.83개의 short-only 후보가 포함됐다. 장기 raw score 최대 절댓값은 `0.175`였다.

통합 품질 원본은 [v3_quality_snapshot_20260827T224522Z.md](diagnostics/v3_quality_snapshot_20260827T224522Z.md), 응답 시간과 전체 경로 결과는 [v3_online_baseline_20260827T230722Z.json](diagnostics/v3_online_baseline_20260827T230722Z.json)이다. 전체 V3 단위 테스트 `118개`와 공용 추천 executor 테스트 `2개`가 통과했다.

## 최종 간이 결과 분석

[최종 ontology 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260827T233031Z.md)는 stable 6명과 drift 6명, 추천 240개를 분석했다.

| 유형 | top-5 칸 | 현재 장르 일치 | 과거 장르만 일치 |
| --- | ---: | ---: | ---: |
| stable | 30 | 30 (100%) | 0 |
| drift | 30 | 18 (60%) | 12 |

해석:

1. stable 사용자는 현재 방향과 일치해 장기 파이프라인의 즉시 수정 근거가 없다.
2. drift 사용자는 short-only 후보가 들어왔지만 과거 장기 model 후보가 일부 top-5/top-10에 남았다.
3. 어벤져스, 인피니티 워, 데드풀, 펄프 픽션 등의 반복은 작은 합성 학습 집단과 공통 인기 방향의 영향을 받을 수 있다.
4. 한 영화의 과도한 장르 metadata는 ontology 일치를 부풀릴 수 있다.
5. ontology는 이 현상의 의미 근거를 보여주지만 LightFM 반복의 인과 원인을 증명하지 않는다.

## Phase G. Drift 행동 포함 전체 재학습

Phase B와 같은 설정을 고정하고 post-model 저장 행동을 학습 dataset에 포함했다.

- positive pair: `3,373 → 3,445` (`12명 x 6편`, 72건 증가)
- 사용자: `128명` 유지
- model health: 통과
- 후보 게시: `128명 x 150개`, 실패 0건
- model: `hybrid-589adbba344c-abb9c7b0706d-bf89dc0a3ba9-45932f2c79ee-9e3651b419af-7b869d3b`
- candidate: `cand-ca26023f3445afbcc294eb20`
- bundle: `bundle-31188df3ab847d4e31287cfc`

| 항목 | 재학습 전 | 재학습 후 |
| --- | ---: | ---: |
| drift 장기 top-20 최근 장르 포함 | 26.7% | 40.0% |
| drift 장기 top-20 과거 장르 포함 | 81.7% | 60.0% |
| stable 장기 top-20 현재 장르 포함 | 89.2% | 60.8% |
| 장기 top-20 고유 영화 | 86편 | 45편 |
| drift 최종 top-5 현재 장르 일치 | 18/30 | 16/30 |
| drift 최종 top-5 과거 장르만 일치 | 12/30 | 14/30 |
| 사용자 간 top-5 반복 규칙 | 17건 | 25건 |

새 행동 자체는 장기 후보를 최근 방향으로 이동시켰다. 그러나 사용자 공통 후보 집중이 더 커져 stable 품질과 최종 drift top-5가 악화됐다. 따라서 문제를 단기 후보 갱신 누락으로 보지 않고 LightFM 학습 데이터, feature 표현과 작은 합성 집단의 협업 일반화 문제로 확정한다. hard filter 위반과 최종 중복은 계속 0건이었다.

원본은 [Phase G 품질 결과](diagnostics/v3_quality_snapshot_20260828T042559Z.md)와 [Phase G 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260828T042637Z.md)다. cache 최초 생성 결과와 warm 결과의 장기·단기·최종 영화 순서 hash는 동일했다.

## 현재 결론과 보류

지금 추가 점수 조정은 하지 않는다.

drift 행동 포함 전체 재학습으로 장기 학습 문제를 확인했다. 다음 품질 작업은 stable 회귀와 공통 인기 후보 과집중을 함께 줄여야 하며, 단기 lane 비율만 높이는 방식은 사용하지 않는다.

다음 항목도 현재 보류한다.

- 작은 합성 표본만 근거로 한 인기 영화 추가 감점
- 단기 lane 비율 추가 상향
- ontology weight 추가 변경
- 실제 사용자 없이 협업 필터링 품질 확정
- NDCG·Recall 평가

후속 검증과 재개 순서는 [08 후속 작업](08_additional_work_backlog.md) P2를 따른다.

## Phase H. 연속 시간 감쇠와 독립 장기 ontology 후보

Phase G에서 확인한 두 문제를 분리해 수정했다.

1. 학습·장기 profile의 구간 감쇠를 행동별 연속 half-life로 교체했다. saved/pinned 60일, watched 180일, passed 90일이며 favorite은 명시 취향으로 감쇠하지 않는다. 오래된 행동의 최저 배율은 0.05이고 timestamp가 없는 mutable 행동은 0.25만 인정한다.
2. known user도 LightFM top-150과 별도로 장기 profile feature 기반 ontology 후보 100개를 조회한다. 모델·장기 ontology·단기 점수는 각각 정규화하고 원점수와 source를 분리 기록한다.
3. 모델과 장기 ontology 상위 50개의 일치율로 LightFM 비중을 45~65% 사이에서 제한한다. 장기 ontology 후보는 상세 분석 전 100개에 최소 20%가 들어오도록 보호한다.

활성 artifact는 다음과 같다.

- model: `hybrid-02e666e23f10-d8dd44e869db-e2a5a2a2e0ca-45932f2c79ee-9e3651b419af-7b869d3b`
- candidate: `cand-dd6dd505d38733bfb53d2aa8`
- bundle: `bundle-ff3d35e49ba03cc72adc9eed`
- training: positive pair 3,445, model health 통과
- materialization: 128명 x 150개, 실패 0, 3.64초

| 항목 | Phase G | Phase H |
| --- | ---: | ---: |
| LightFM 장기 top-20 고유 영화 | 45 | 45 |
| 최종 top-20 고유 영화 | 108 | 160 |
| 최종 top-5 고유 영화 | 34 | 42 |
| stable 최종 장기 장르 share | 0.564 | 0.680 |
| drift 최종 단기 장르 share | 0.382 | 0.580 |
| drift top-5 현재 장르 일치 | 16/30 | 22/30 |
| 반복 top-5 규칙 | 25 | 13 |
| top-10 현재 장르 불일치 | 31 | 15 |
| top-10 저투표 이상치 | 1 | 8 |

판정은 부분 개선이다. 장기 ontology 후보가 LightFM 풀 밖에서 실제로 들어왔고 최종 취향 일치와 사용자 간 다양성이 개선됐다. 그러나 LightFM 자체 top-20 집중은 45편으로 그대로이며, ontology/short lane이 저투표·과도한 장르 metadata 영화를 끌어올리는 새 문제가 커졌다. 따라서 다음 단계는 ontology 비중을 다시 임의 조정하는 것이 아니라 source별 catalog trust를 먼저 보완하고, 실제 사용자 규모에서 협업 과집중을 재검증하는 것이다.

원본은 [Phase H 품질 결과](diagnostics/v3_quality_snapshot_20260828T050041Z.md)와 [Phase H 이상치 감사](diagnostics/v3_ontology_outlier_audit_20260828T110011Z.md)다. 이 결과는 정답 기반 relevance 평가가 아니라 stable 6명·drift 6명의 방향성 감사다.
