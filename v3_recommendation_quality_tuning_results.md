# V3 장기 취향 튜닝 결과와 현재 판단

## 1. 문서 역할

이 문서는 완료된 실험에서 현재도 필요한 판단만 남긴다. 앞으로 수행할 작업은 `v3_recommendation_quality_tuning_plan.md`를 따른다.

2026-09-14 정리에서 결론이 이 문서에 반영된 일회성 실험 실행기, 비활성 shadow 모델과 원시 `outputs/` 파일을 제거했다. 아래 결과 경로는 당시 실행 위치를 나타내는 이력이며, 현재 비교의 기준은 이 문서에 남긴 집계값과 판정이다. 개발 사용자와 이미 소비된 일반화 사용자를 구분하는 평가 계획은 유지한다.

과거 집중형 100명 결과는 개발 과정의 증거이며 최종 일반화 결과가 아니다. 2026-09-11 matched 200명 재검사에서 결과가 뒤집힌 항목은 기존 채택 상태를 해제했다.

## 2. NDCG@100% 순수 LightFM과 V3 비교

평가 정답과 주 지표를 다음과 같이 다시 고정했다.

- 정답 등급: `PASS=0`, `NEUTRAL=1`, `PIN=2`, `SAVED=3`
- 주 지표: 각 사용자의 holdout 전체를 평가하는 `NDCG@100%`
- 보조 지표: `ceil(holdout 수 * 0.20)`까지 평가하는 `NDCG@20%`
- 후보: 시간순 뒤 30%에 실제로 평가한 영화만 사용
- 비교 집단: 안정 집중형 100명과 안정 다취향형 100명
- 반복: 같은 집단을 seed `42/43/44`로 학습한 평균

여기서 P0는 identity-only logistic LightFM이고, E는 `logistic + PASS 음수`, 현재 행동 weight와 365일 감쇠, 영화 ontology, 장기 사용자 ontology, confidence와 centering을 적용한 최신 shadow V3다.

| 구성 | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| P0 순수 LightFM | 0.873887 | 0.659381 |
| E 최신 shadow V3 | 0.865111 | 0.636774 |
| E - P0 | -0.008777 | -0.022606 |

직접 비교한 600개 사용자-seed 쌍에서 V3는 201개 상승, 396개 하락, 3개 동일이었다. 두 사용자 유형 모두 하락했다.

| 사용자 유형 | P0 NDCG@100% | E NDCG@100% | 차이 |
| --- | ---: | ---: | ---: |
| 안정 다취향형 | 0.874684 | 0.866675 | -0.008009 |
| 안정 집중형 | 0.873091 | 0.863546 | -0.009544 |

단계별 평균 변화:

| 구성요소 | 비교 | NDCG@100% 차이 | 판단 |
| --- | --- | ---: | --- |
| V3 행동·학습 정책 | P0 -> A | -0.002285 | 손실 |
| 영화 ontology | A -> B | -0.006096 | 가장 큰 손실 |
| 장기 사용자 ontology | B -> D | +0.000141 | 사실상 중립 |
| confidence와 centering | D -> E | -0.000537 | 작은 손실 |

현재 설정에서는 영화 ontology를 LightFM item feature로 결합한 구간이 가장 큰 하락 지점이다. 이는 ontology가 추천에 불필요하다는 결론이 아니라, 현재 feature 크기와 결합 방식이 이 순위 판별 목표에서 이득을 만들지 못했다는 뜻이다. 온보딩 정보가 없는 MovieLens 사용자이므로 온보딩 사용자 ontology의 독립 효과는 측정하지 못했다.

세 V3 모델 모두 후보 집중도 health gate에서 실패해 population collaborative confidence가 `0`이었다. 다만 confidence와 centering 구간의 평균 손실은 `-0.000537`이고, 더 큰 손실은 그 이전 영화 ontology 구간에서 이미 발생했다. 이번 비교로 운영 model, candidate snapshot, bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/ndcg100_pure_vs_v3/`

`0.873887`은 영화의 정확한 전체 순서를 87% 맞혔다는 뜻이 아니다. 4단계 관련도와 순위 할인으로 계산한 상대 점수이며, 모델 비교에는 같은 정답·후보·사용자를 사용한 차이를 본다.

### NDCG@100% Loss와 PASS 확정

영화·사용자 ontology와 serving 보정을 제외한 identity-only 입력에서 LightFM 학습 목표만 분리했다. matched 200명과 seed `42/43/44`는 위 비교와 같다.

| 변형 | NDCG@100% | 보조 NDCG@20% |
| --- | ---: | ---: |
| WARP, PASS 제외 | 0.869091 | 0.647353 |
| Logistic, PASS 제외 | 0.870421 | 0.647266 |
| Logistic, PASS 음수 포함 | 0.871602 | 0.650751 |

판단:

- Logistic으로 loss를 바꾼 효과는 NDCG@100% `+0.001330`이었다.
- Logistic에서 PASS 음수를 추가한 효과는 `+0.001181`이었다.
- 두 변경의 합은 WARP 대비 `+0.002511`이었다.
- 세 seed와 안정 집중형·안정 다취향형에서 모두 `Logistic + PASS 음수`가 1위였다.
- WARP 대비 600쌍 중 349개 상승, 248개 하락, 3개 동일이었다.
- PASS 추가만 비교하면 402개 상승, 191개 하락, 7개 동일이었다.
- R1을 완료하고 이후 shadow 학습 목표를 `logistic + 명시적 PASS 음수`로 고정한다.
- 이 실험은 shadow 비교이며 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/loss_pass_multiseed_ndcg100/`

### NDCG@100% 행동 weight와 학습 보정

R1의 `Logistic + PASS 음수`와 identity-only 입력을 고정하고 각 단계에서 한 종류만 변경했다.

| 단계 | 기준 | 변경 | NDCG@100% 변화 | 판단 |
| --- | --- | --- | ---: | --- |
| 행동 가중치 | SAVED/PINNED `2.0/2.0` | `1.5/1.0` | +0.000633 | 변경 선택 |
| 긍정 시간 감쇠 | 365일 | 없음 | +0.000136 | 365일 유지 |
| 사용자 활동량 보정 | cap at median | 없음 | +0.000325 | shadow 잠정 선택 |
| item-frequency 보정 | none | inverse sqrt | -0.000971 | none 유지 |

세부 판단:

- `1.5/1.0`은 세 seed와 두 사용자 유형에서 모두 우세했고 600쌍 중 364개 상승, 226개 하락이었다.
- 감쇠 없음은 seed별 승자가 갈렸고 안정 집중형에서 `-0.000476` 하락했다. 평균의 작은 상승보다 정책 의미와 유형 안정성을 우선해 365일을 유지한다.
- user-activity cap 제거는 세 seed와 두 유형에서 모두 우세했고 600쌍 중 347개 상승, 243개 하락이었다. 다만 최종 hybrid 후보의 사용자 간 과집중 검사를 통과하기 전에는 확정하지 않는다.
- inverse-sqrt item 보정은 600쌍 중 371개를 하락시켰으므로 학습 단계에 다시 넣지 않는다.

R2 선택을 누적한 identity-only shadow 측정값은 NDCG@100% `0.872560`이다. R1 직후 `0.871602`보다 `+0.000958`이지만 순수 LightFM `0.873887`보다는 `-0.001327` 낮다. 이 차이는 이후 ontology 결합 실험과 분리해 유지한다.

결과:

- `tests/v3_long_term_eval/outputs/action_weights_ndcg100/`
- `tests/v3_long_term_eval/outputs/recency_ndcg100/`
- `tests/v3_long_term_eval/outputs/training_corrections_ndcg100/`

### NDCG@100% 영화 ontology 재결합

R2의 identity-only `0.872560`을 기준으로 영화 semantic block의 전체 크기만 변경했다.

| item semantic scale | NDCG@100% | identity 대비 |
| ---: | ---: | ---: |
| 0.00 | 0.872560 | +0.000000 |
| 0.25 | 0.872341 | -0.000219 |
| 0.50 | 0.870506 | -0.002054 |
| 0.75 | 0.868251 | -0.004309 |
| 1.00 | 0.865954 | -0.006606 |

semantic 강도가 커질수록 점수가 단조롭게 하락했다. 전체 semantic의 낮은 scale에서도 이득이 없어 genre-only를 추가로 분리했다.

| genre scale | NDCG@100% | identity 대비 |
| ---: | ---: | ---: |
| 0.00 | 0.872560 | +0.000000 |
| 0.25 | 0.872555 | -0.000005 |
| 0.50 | 0.870537 | -0.002022 |
| 1.00 | 0.866319 | -0.006241 |

genre `0.25`는 전체 평균이 사실상 같았지만 안정 다취향형에서 `+0.000325`, 안정 집중형에서 `-0.000336`으로 방향이 갈렸다. 명확한 이득이 아니므로 이후 장기 취향 LightFM item 입력은 identity-only를 shadow 기준으로 사용한다.

이 결과는 ontology를 V3에서 제거한다는 뜻이 아니다. 전체 카탈로그 후보 검색, cold item, 콜드스타트, 단기 취향, 의미 근거와 정책 판단은 이 평가 범위 밖이며 ontology 계층에 그대로 남는다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/item_semantic_scale_ndcg100/`
- `tests/v3_long_term_eval/outputs/genre_only_scale_ndcg100/`

### NDCG@100% 장기 사용자 ontology

R3에서 선택한 item identity-only 위에 현재 U2 `bounded_long_term_evidence` 사용자 프로필을 추가했다. semantic weight는 현재값 `0.5`, 사용자 identity weight는 `2.0`이다.

| 사용자 입력 | NDCG@100% | NDCG@20% | identity 대비 |
| --- | ---: | ---: | ---: |
| identity-only | 0.872560 | 0.654932 | +0.000000 |
| 현재 U2 전체 profile | 0.872772 | 0.654786 | +0.000213 |

사용자별로는 600쌍 중 340개 상승, 249개 하락, 11개 동일이었다. 유형별 변화는 안정 집중형 `+0.000562`, 안정 다취향형 `-0.000137`이었다. 이 결과만으로 profile을 확정하지 않고 Top-K 범위를 비교했다.

| profile Top-K | NDCG@100% | NDCG@20% | identity 대비 |
| --- | ---: | ---: | ---: |
| identity-only | 0.872560 | 0.654932 | +0.000000 |
| compact | 0.872738 | 0.654401 | +0.000178 |
| current | 0.872772 | 0.654786 | +0.000213 |
| expanded | 0.872952 | 0.655186 | +0.000392 |

`expanded`는 `genre=12`, `keyword=60`, `actor=60`, `director=24`, `theme=24`, `mood=20`이다. 세 seed 모두 identity-only보다 높았고, 유형별 변화도 안정 집중형 `+0.000725`, 안정 다취향형 `+0.000060`으로 같은 방향이었다.

선택된 `expanded` profile에서 user identity weight `2.0`을 고정하고 semantic weight를 비교했다.

| user semantic weight | NDCG@100% | NDCG@20% | identity 대비 |
| ---: | ---: | ---: | ---: |
| 0.25 | 0.872777 | 0.654782 | +0.000217 |
| 0.50 | 0.872952 | 0.655186 | +0.000392 |
| 0.75 | 0.873249 | 0.656152 | +0.000690 |

`0.75`는 세 seed 모두 identity-only보다 `+0.000514~+0.000895` 높았다. 유형별 변화는 안정 집중형 `+0.001287`, 안정 다취향형 `+0.000093`이었다. 600개 사용자-seed 쌍 중 350개 상승, 242개 하락, 8개 동일이었다.

R4 개발 집단 shadow 선택은 `expanded + semantic weight 0.75`였다. 이 시점의 누적 장기 취향 shadow 값 `0.873249`는 순수 LightFM `0.873887`보다 `-0.000638` 낮았다. 이후 R7 미사용 집단에서 사용자 ontology 이득이 재현되지 않았으므로 전 사용자 공통 적용 결정은 최종 채택 해제했다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/user_profile_ndcg100/`
- `tests/v3_long_term_eval/outputs/user_profile_grid_ndcg100/TUNE-11/20260913T115550Z_matched_seeds-42-43-44/`

### NDCG@100% 모델 용량과 규제

R1-R4의 shadow 선택을 고정하고 전체 조합이 아닌 단계별 비교를 수행했다.

| latent dimension | NDCG@100% | NDCG@20% |
| ---: | ---: | ---: |
| 16 | 0.873299 | 0.656059 |
| 32 | 0.873249 | 0.656152 |
| 64 | 0.873054 | 0.655697 |
| 96 | 0.873114 | 0.655996 |

16과 32의 NDCG@100% 차이는 `+0.000050`으로 작다. 비슷한 품질이면 작은 모델을 사용한다는 사전 원칙에 따라 16차원을 선택했다.

| user/item alpha | NDCG@100% | NDCG@20% |
| ---: | ---: | ---: |
| 1e-6 | 0.873237 | 0.655871 |
| 1e-5 | 0.873299 | 0.656059 |
| 1e-4 | 0.873097 | 0.655440 |

`1e-5`가 세 seed와 두 사용자 유형에서 모두 가장 안정적이므로 유지한다.

| epochs | NDCG@100% | NDCG@20% |
| ---: | ---: | ---: |
| 10 | 0.872550 | 0.654141 |
| 20 | 0.873299 | 0.656059 |
| 40 | 0.873923 | 0.658424 |
| 60 | 0.875275 | 0.662956 |

60 epochs가 시험 상한에서도 계속 상승해 learning rate를 추가 비교했다.

| learning rate | NDCG@100% | NDCG@20% | 0.01 대비 |
| ---: | ---: | ---: | ---: |
| 0.005 | 0.873461 | 0.656879 | -0.001813 |
| 0.010 | 0.875275 | 0.662956 | +0.000000 |
| 0.025 | 0.877761 | 0.667259 | +0.002487 |
| 0.050 | 0.877925 | 0.666190 | +0.002650 |

`0.05`의 NDCG@100%가 `0.025`보다 `+0.000164` 높지만 seed별 승자가 갈렸고 NDCG@20%는 오히려 `-0.001069` 낮았다. `0.025`는 기존값보다 세 seed와 두 사용자 유형에서 모두 상승했으므로 이를 선택한다.

R5 최종 shadow 설정은 `dimension=16`, `alpha=1e-5`, `epochs=60`, `learning rate=0.025`다. NDCG@100% `0.877761`로 순수 LightFM 기준선 `0.873887`보다 `+0.003875` 높다. 이 차이는 아직 개발 사용자에서 얻은 결과이며 최종 일반화 증거는 아니다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/model_capacity_ndcg100/TUNE-12/20260913T121549Z_matched_seeds-42-43-44/`
- `tests/v3_long_term_eval/outputs/learning_rate_ndcg100/TUNE-13/20260913T122715Z_matched_seeds-42-43-44/`

### NDCG@100% confidence와 centering

R5 모델을 seed별 한 번만 학습한 뒤 collaborative confidence on/off와 centering `0/0.25/0.5/0.75`를 조합 평가했다. 이번 200명의 평균 population confidence는 `0.18`이다.

| confidence | centering | NDCG@100% | NDCG@20% | Top-20 Jaccard | Top-20 unique ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| off | 0.00 | 0.877761 | 0.667259 | 0.425616 | 0.302083 |
| off | 0.25 | 0.877294 | 0.665857 | 0.325533 | 0.375000 |
| off | 0.50 | 0.873657 | 0.656463 | 0.167770 | 0.510417 |
| off | 0.75 | 0.862048 | 0.626524 | 0.039355 | 0.789583 |
| on | 0.00 | 0.877751 | 0.666086 | 0.698995 | 0.187500 |
| on | 0.25 | 0.877996 | 0.666486 | 0.661052 | 0.195833 |
| on | 0.50 | 0.878202 | 0.666725 | 0.564749 | 0.227083 |
| on | 0.75 | 0.877552 | 0.664798 | 0.296214 | 0.404167 |

confidence on, centering `0.5`가 NDCG@100%만 보면 원점수보다 `+0.000440` 높았다. 그러나 seed 44에서 하락했고, 사용자별로 상승 284개보다 하락 309개가 많았으며 Top-20 겹침도 `0.425616 -> 0.564749`로 악화됐다.

현재 confidence는 협업 신뢰도가 낮을 때 user identity만 줄인다. 이번 모델에서는 그 결과 사용자별 차이가 약해지고 공통 semantic과 item bias가 상대적으로 커져 모든 사용자에게 비슷한 영화가 더 많이 노출됐다. 과집중 완화라는 장치의 목적과 반대이므로 이 사용자군에는 채택하지 않는다.

centering 단독 적용은 집중도를 낮췄지만 강도가 커질수록 NDCG 손실이 커졌다. 장기 행동이 충분한 matched 사용자에 대한 R6 shadow 선택은 confidence 감쇄 없음, centering `0`이다. sparse/cold user의 confidence 정책을 전역 삭제한다는 결론은 아니며 별도 사용자군 검증이 필요하다.

인기 영화 억제는 LightFM이 취향 순위를 학습하는 이 평가에 섞지 않는다. 이후 전체 카탈로그 후보 검색과 최종 재정렬에서 bounded popularity 정책으로 검증한다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/serving_adjustment_ndcg100/TUNE-14/20260913T123839Z_matched_seeds-42-43-44/`

### 미사용 사용자 일반화 검증

기존 개발 사용자 200명을 제외하고 같은 선발 기준으로 안정 집중형 100명과 안정 다취향형 100명을 구성했다. 두 집단의 source user ID 중복은 0명이다. 각 사용자 유형은 dominant genre와 활동량을 쌍으로 맞췄고 평균 활동량 차이는 약 1.45%였다.

| 구성 | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| 순수 LightFM | 0.880300 | 0.664478 |
| R5 V3 identity-only | 0.882526 | 0.672562 |
| R5 V3 + R4 사용자 ontology | 0.882324 | 0.670628 |

| 비교 | 평균 차이 | 사용자 bootstrap 95% CI |
| --- | ---: | ---: |
| 최종 V3 - 순수 LightFM | +0.002024 | [-0.001388, +0.005625] |
| 최종 V3 - V3 identity-only | -0.000202 | [-0.001086, +0.000665] |
| V3 identity-only - 순수 LightFM | +0.002225 | [-0.001271, +0.006145] |

최종 V3는 세 seed에서 모두 순수 LightFM보다 높았지만 사용자 단위 신뢰구간이 0을 포함해 반복 가능한 우위를 확정하지 못했다. 유형별로는 안정 집중형이 순수 LightFM보다 `+0.004490`, 안정 다취향형은 `-0.000442`였다.

사용자 ontology는 V3 identity-only 대비 안정 집중형에서 `+0.000264`, 안정 다취향형에서 `-0.000667`이었다. 개발 집단에서 선택한 전역 `expanded + 0.75` profile이 다취향 사용자에게 일반화되지 않았다는 뜻이다.

R7 gate는 실패했다. R4 전역 profile 선택은 해제하고, 다음 반복에서는 장기 취향의 집중도와 근거량에 따라 사용자 semantic 강도를 연속적으로 조절하는 가설을 개발 집단에서 검증한다. 이 일반화 집단은 후속 튜닝에 재사용하지 않으며 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/generated/generalization_profiles/`
- `tests/v3_long_term_eval/outputs/final_generalization_ndcg100/TUNE-15/20260913T125149Z_untouched_seeds-42-43-44/`

### 사용자 semantic 집중도 confidence

R7에서 제기된 가설을 기존 개발 사용자 200명에서만 확인했다. 사용자 유형 라벨이나 holdout 정답은 학습 조건으로 사용하지 않았다. 실제 장기 genre feature의 정규화 entropy로 집중도를 계산하고, 기존 `n/(n+5)` 근거량 감쇠는 유지한 채 semantic weight를 `0.75 * sqrt(1 - normalized_entropy)`로 연속 조절했다.

| 사용자 입력 | NDCG@100% | NDCG@20% | identity 대비 |
| --- | ---: | ---: | ---: |
| identity-only | 0.879018 | 0.670060 | +0.000000 |
| 고정 semantic 0.75 | 0.877202 | 0.665675 | -0.001816 |
| 집중도 연속 조절 | 0.878628 | 0.668993 | -0.000390 |

연속 조절은 고정 `0.75`보다 `+0.001426` 높았고 사용자 bootstrap 95% 신뢰구간도 `[+0.000596, +0.002310]`이었다. 그러나 identity-only 대비 차이는 `-0.000390`, 신뢰구간 `[-0.001279, +0.000596]`으로 우위를 만들지 못했다.

집중형의 평균 적용 weight는 `0.4123`, 다취향형은 `0.3448`로 의도한 차이는 만들어졌다. 하지만 집중도와 사용자별 NDCG 변화의 Pearson 상관은 고정 방식 `0.041`, 연속 방식 `0.024`에 불과했고 사분위 결과도 단조롭지 않았다. 긍정 근거 수와 효과의 상관도 사실상 `0`이었으며, 이번 사용자들의 평균 근거 confidence가 이미 `0.966`으로 포화된 영향이 있다.

따라서 장르 집중도와 근거량만으로 사용자 semantic 전체를 조절하는 방식은 채택하지 않는다. 다음 확인은 사용자 semantic을 genre, keyword, actor, director, theme, mood로 분해해 실제 손실 family를 찾는 것이다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/user_semantic_confidence_ndcg100/TUNE-16/20260913T165808Z_development_seeds-42-43-44/`

### 사용자 semantic family 독립 분해

R5 최종 조건과 개발 사용자 200명을 고정하고 `genre`, `keyword`, `actor`, `director`, `theme`, `mood`를 각각 단독 사용자 feature로 학습했다. 선택하지 않은 family는 0으로 두고 그 weight를 남은 family에 재분배하지 않았다.

| 사용자 입력 | NDCG@100% | identity 대비 | 사용자 bootstrap 95% CI |
| --- | ---: | ---: | --- |
| identity-only | 0.879018 | +0.000000 | - |
| genre | 0.877538 | -0.001480 | [-0.002862, -0.000161] |
| keyword | 0.879292 | +0.000273 | [-0.000131, +0.000715] |
| actor | 0.879314 | +0.000296 | [-0.000114, +0.000743] |
| director | 0.879265 | +0.000247 | [-0.000156, +0.000681] |
| theme | 0.878949 | -0.000069 | [-0.000528, +0.000402] |
| mood | 0.879055 | +0.000037 | [-0.000355, +0.000464] |

`genre`는 두 사용자 유형 모두 하락했고 신뢰구간도 0 아래여서 사용자 LightFM feature의 명확한 손실 요인이다. `keyword`, `actor`, `director`는 세 seed에서 평균 상승했으며 집중형에는 각각 약 `+0.00059`, `+0.00063`, `+0.00053` 도움이 됐다. 그러나 다취향형은 각각 약 `-0.00005`, `-0.00003`, `-0.00004`로 아주 작게 하락했고 신뢰구간도 0을 포함했다.

따라서 독립 채택 gate를 통과한 family는 없다. 다만 고정 전체 semantic의 손실 `-0.001816` 중 상당 부분이 genre 단독 손실 `-0.001480`과 연결되므로, 다음에는 `genre 제외 전체`와 평균 방향이 양수였던 `keyword+actor+director` 조합만 확인한다. 두 조합도 실패하면 장기 취향 LightFM 사용자 입력은 identity-only를 선택한다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/user_semantic_family_ndcg100/TUNE-17/20260913T171324Z_development_seeds-42-43-44/`

### 사용자 semantic 최종 조합

`TUNE-17` 결과에서 마지막 가능성으로 남긴 두 조합을 R5 최종 조건에서 비교했다. 제외된 family의 weight는 재분배하지 않았고 행 전체 재정규화도 하지 않았다.

| 사용자 입력 | NDCG@100% | NDCG@20% | identity 대비 | 사용자 bootstrap 95% CI |
| --- | ---: | ---: | ---: | --- |
| identity-only | 0.879018 | 0.670060 | +0.000000 | - |
| genre 제외 전체 | 0.878802 | 0.668939 | -0.000216 | [-0.000803, +0.000403] |
| keyword+actor+director | 0.879301 | 0.670892 | +0.000283 | [-0.000126, +0.000725] |

`genre 제외 전체`는 평균부터 identity-only보다 낮았다. `keyword+actor+director`는 평균과 seed 42·43에서 소폭 높았지만 seed 44에서 `-0.000028`, 다취향형에서 `-0.000012` 낮았고 신뢰구간도 0을 포함했다. 따라서 두 조합 모두 사전에 정한 일관성 gate를 통과하지 못했다.

장기 취향 LightFM의 사용자 입력은 identity-only로 확정한다. 사용자 ontology를 LightFM에 억지로 남기지 않으며, ontology 자체는 독립 후보 검색·콜드스타트·단기 취향·의미 근거와 정책 계층에 유지한다. 운영 모델과 bundle은 변경하지 않았다.

R5의 dimension·epochs·learning rate는 사용자 semantic `0.75`가 포함된 상태에서 선택됐으므로, 다음에는 identity-only 입력에서 모델 설정을 좁게 재확인해야 한다.

결과:

- `tests/v3_long_term_eval/outputs/user_semantic_combination_ndcg100/TUNE-18/20260913T173134Z_development_seeds-42-43-44/`

### Identity-only 모델 설정 재확인

장기 취향 LightFM 사용자 입력을 identity-only로 확정했으므로, 사용자 semantic `0.75`가 포함된 상태에서 선택했던 R5 모델 설정을 다시 확인했다. alpha `1e-5`를 고정하고 dimension `16/32/64`, epochs `20/40/60`, learning rate `0.01/0.025/0.05`를 전체 Cartesian grid가 아닌 순차 방식으로 비교했다.

| 단계 | 후보 | NDCG@100% 최고 |
| --- | --- | ---: |
| dimension, E20/LR0.01 | 16 / 32 / 64 | 32: 0.872614 |
| epochs, D32/LR0.01 | 20 / 40 / 60 | 60: 0.876784 |
| learning rate, D32/E60 | 0.01 / 0.025 / 0.05 | 0.025: 0.878925 |

순차 grid 최고 `D32/E60/LR0.025`를 기존 `D16/E60/LR0.025`와 직접 비교한 결과는 다음과 같다.

| 설정 | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| 기존 D16/E60/LR0.025 | 0.879018 | 0.670060 |
| grid 최고 D32/E60/LR0.025 | 0.878925 | 0.670085 |

32차원은 기존 대비 NDCG@100% `-0.000093`, 사용자 bootstrap 95% 신뢰구간 `[-0.001549, +0.001446]`이었다. 다취향형은 `+0.001286` 높았지만 집중형은 `-0.001472` 낮았고 사용자-seed 쌍도 상승 277개보다 하락 318개가 많았다.

따라서 더 큰 모델로 바꿀 근거가 없으며 `16차원/alpha 1e-5/60 epochs/learning rate 0.025`를 최종 identity-only 개발 설정으로 유지한다. 개발 NDCG@100%는 `0.879018`이다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/identity_model_ndcg100/TUNE-19/20260913T173749Z_development_seeds-42-43-44/`

### Identity-only V3 두 번째 미사용 사용자 일반화

기존 개발 사용자 200명과 첫 일반화 R7 사용자 200명을 모두 제외하고 새 집중형 100명·다취향형 100명을 만들었다. 세 집단 사이 source user 중복은 0명이다. 새 집단은 Action 26쌍, Comedy 37쌍, Drama 37쌍이며, 집중형과 다취향형의 평균 활동량 차이는 `0.36%`다.

| 구성 | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| 순수 LightFM | 0.886185 | 0.677765 |
| identity-only V3 | 0.886202 | 0.680440 |
| V3 - pure | +0.000017 | +0.002675 |

NDCG@100%의 사용자 bootstrap 95% 신뢰구간은 `[-0.002675, +0.002601]`이다. seed별 차이는 `42=+0.000405`, `43=-0.000206`, `44=-0.000149`로 방향이 갈렸다. 사용자-seed 쌍은 상승 315개, 하락 282개, 동일 3개였다.

| 사용자 유형 | 순수 LightFM | identity-only V3 | 차이 |
| --- | ---: | ---: | ---: |
| 안정 집중형 | 0.883125 | 0.884577 | +0.001452 |
| 안정 다취향형 | 0.889245 | 0.887827 | -0.001419 |

V3는 상위 20% 순서에서는 세 seed 평균으로 순수 LightFM보다 높았지만, 주 지표 NDCG@100%는 사실상 같고 seed와 사용자 유형의 방향도 일치하지 않았다. 따라서 “V3가 순수 LightFM보다 장기 취향 순위를 일반적으로 더 잘 맞힌다”는 gate는 실패했다.

추가 hyperparameter 수치 탐색은 중단하지만 장기 순위 품질 단계 자체는 종료하지 않는다. 장기 LightFM은 identity-only와 기존 `D16/E60/LR0.025`를 현재 비교 기준으로 유지하고, 순수 LightFM과 다른 학습 행동·identity 표현·모델 설정의 효과를 개발 집단에서 다시 분해한다. 손실 원인을 수정해 미사용 일반화 집단에서 NDCG@100% 우위를 확인하기 전에는 전체 카탈로그 후보 검색 단계로 이동하지 않는다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/generated/identity_generalization_profiles/`
- `tests/v3_long_term_eval/outputs/identity_generalization_ndcg100/TUNE-20/20260913T174502Z_untouched_seeds-42-43-44/`

### LightFM core 2x2x2 통제 실험

TUNE-21에서는 영화·사용자 ontology와 serving 보정을 모두 제외하고 행동 입력, identity 표현, 학습 설정만 각각 두 수준으로 조합했다. 개발 사용자 200명과 seed `42/43/44`에서 총 24회 학습했다.

| 행동 입력 | identity | 학습 설정 | NDCG@100% | NDCG@20% |
| --- | --- | --- | ---: | ---: |
| baseline | implicit | baseline | 0.873726 | 0.658670 |
| baseline | implicit | V3 | 0.878907 | 0.671110 |
| baseline | supported | baseline | 0.876613 | 0.665386 |
| baseline | supported | V3 | 0.876875 | 0.664477 |
| V3 | implicit | baseline | 0.871921 | 0.652231 |
| V3 | implicit | V3 | 0.877995 | 0.666051 |
| V3 | supported | baseline | 0.872619 | 0.654547 |
| V3 | supported | V3 | 0.878609 | 0.667241 |

기존 문서 기준점은 실행 편차 `0.0005` 이내로 재현됐다. 세 요인의 평균 효과는 V3 행동 `-0.001244`, supported identity `+0.000542`, V3 학습 설정 `+0.004377`이었다. 학습 설정만 사용자 bootstrap 95% 신뢰구간 `[+0.001006, +0.008336]`, 세 seed와 두 사용자 유형에서 모두 같은 방향이었다.

하지만 개별 효과는 서로 독립적이지 않았다. identity와 학습 설정의 상호작용은 `-0.002502`, 세 요인의 상호작용은 `+0.004837`이었다. 실제 V3 조건에서는 supported identity가 implicit identity보다 `+0.000614`였지만, baseline 행동과 V3 학습 설정에서는 `-0.002033`이었다. V3 행동도 실제 V3 identity·설정에서는 `+0.001735`였으나 다른 세 조건에서는 하락했다.

따라서 현재 결과는 단일 행동 weight나 identity weight가 명백한 결함이라는 증거가 아니다. 개발 집단에서 V3 전체는 순수 기준보다 `+0.004883`이지만, baseline 행동·implicit identity에 V3 학습 설정만 적용한 단순 모델보다 `-0.000298` 낮다. 현재 개선의 대부분은 학습 설정에서 왔으며 행동·identity 구조의 추가 이득은 입증되지 않았다.

같은 개발 사용자를 더 튜닝하지 않는다. 기존 개발, R7, TUNE-20 사용자를 제외한 개발 집단 B에서 동일 실험을 한 번 재현한 뒤 수정 대상을 결정한다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/lightfm_factorial_ndcg100/TUNE-21/20260913T194540Z_development_seeds-42-43-44/`

### 새 개발 집단 B 재현

TUNE-22에서는 기존 개발, R7, TUNE-20의 source user 600명을 모두 제외하고 안정 집중형 100명과 안정 다취향형 100명을 새로 선발했다. 새 200명은 이전 각 집단과 교집합이 0명이었고, 유형 간 100쌍의 대표 장르가 모두 같았다. 평균 활동량 차이는 `0.003257`이었다.

| 행동 입력 | identity | 학습 설정 | NDCG@100% | NDCG@20% |
| --- | --- | --- | ---: | ---: |
| baseline | implicit | baseline | 0.878549 | 0.658728 |
| baseline | implicit | V3 | 0.881582 | 0.666519 |
| baseline | supported | baseline | 0.880850 | 0.664775 |
| baseline | supported | V3 | 0.880843 | 0.662325 |
| V3 | implicit | baseline | 0.877532 | 0.654195 |
| V3 | implicit | V3 | 0.881126 | 0.666272 |
| V3 | supported | baseline | 0.878207 | 0.657181 |
| V3 | supported | V3 | 0.882331 | 0.669674 |

V3 전체 조합은 순수 기준보다 `+0.003783` 높았다. 행동 입력 주효과는 `-0.000657`로 불확실했고, supported identity는 `+0.000861`, V3 학습 설정은 `+0.002686`이었다. 행동과 설정 상호작용은 `+0.002346`, 세 요인의 상호작용은 `+0.003570`으로 TUNE-21과 같은 양의 방향과 일관된 판정을 재현했다.

두 개발 집단 모두에서 V3 전체가 순수 기준보다 높았으므로 현재 V3 core 조합을 최종 검증 후보로 고정한다. 다만 개발 B의 대표 장르는 Action 1쌍, Comedy 50쌍, Drama 49쌍으로 좁았으므로 장르 전반의 일반화가 입증된 것은 아니다. 다음 TUNE-23은 지금까지 사용한 800명을 제외한 새 집단에서 순수 기준과 고정 V3 core만 비교한다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/lightfm_factorial_ndcg100/TUNE-22/20260913T195825Z_development_seeds-42-43-44/`

### 고정 V3 core 최종 일반화

TUNE-23에서는 TUNE-22까지 소비한 source user 800명을 제외하고 새 200명을 선발했다. 이 집단으로 추가 설정을 선택하지 않고 순수 기준과 TUNE-21~22에서 고정한 V3 core만 비교했다.

| 구성 | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| 순수 기준 | 0.876445 | 0.652352 |
| 고정 V3 core | 0.879191 | 0.657468 |
| 차이 | +0.002747 | +0.005115 |

NDCG@100% 차이는 세 seed에서 `+0.002245/+0.003118/+0.002877`, 안정 집중형에서 `+0.002593`, 안정 다취향형에서 `+0.002900`이었다. paired-user bootstrap 95% 신뢰구간은 `[+0.000272, +0.005317]`로 0을 벗어났고, 200명 중 112명 상승, 87명 하락, 1명 동일이었다. 사전 gate를 통과했으므로 V3 행동 입력, supported identity와 `D16/E60/LR0.025/alpha 1e-5` 조합을 장기 취향 LightFM core 기준으로 확정한다.

최종 집단은 기존 네 집단과 교집합이 각각 0명이지만 대표 장르가 Action 3쌍, Animation 1쌍, Comedy 48쌍, Drama 48쌍으로 좁다. 따라서 이번 결과는 안정 집중형·다취향형 사용자에서 core 이득이 일반화됐다는 근거이며 모든 장르에서 같은 폭을 보장하는 근거는 아니다. 전체 카탈로그 후보 검색, ontology 결합과 serving 정책 품질은 아직 측정하지 않았다. 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/lightfm_core_generalization_ndcg100/TUNE-23/20260913T200555Z_generalization_seeds-42-43-44/`

## 3. 이전 matched 200명 재검사

이 절부터의 `NDCG@20/@30` 및 과거 `NDCG@20%` 표는 정답 등급과 주 지표 변경 전 기록이다. 구현 이력으로만 유지하며 현재 설정 선택의 최종 근거로 사용하지 않는다.

### 평가 집단

- 안정적인 취향 집중형 100명
- 안정적인 다취향형 100명
- 두 유형의 대표 장르 분포를 동일하게 구성
- 평균 평점 수와 holdout 수를 근접하게 matching
- 시간에 따른 장르 변화가 낮은 사용자만 사용
- 시간순 70% 학습, 30% 영화 내부 순위 평가
- seed 42

### 전체 구성요소 분해

| 단계 | 구성 | NDCG@20 | NDCG@30 |
| --- | --- | ---: | ---: |
| P0 | 순수 identity LightFM | 0.459511 | 0.497123 |
| A | 현재 V3 행동 정책 + identity-only | 0.434003 | 0.471241 |
| B | A + 영화 ontology | 0.443981 | 0.481902 |
| D | B + 장기 사용자 ontology | 0.441934 | 0.482635 |
| E | D + confidence와 centering | 0.439608 | 0.478932 |

현재 최종 V3는 P0보다 NDCG@20 `-0.019902`, NDCG@30 `-0.018191` 낮다.

사용자 유형별 P0 대비 E 차이:

| 유형 | NDCG@20 차이 | NDCG@30 차이 |
| --- | ---: | ---: |
| 안정 집중형 | -0.021716 | -0.015558 |
| 안정 다취향형 | -0.018090 | -0.020824 |

두 유형 모두 같은 방향이므로 특정 사용자 유형만의 문제가 아니다.

### 순수 LightFM이 더 높게 나온 이유

여기서 순수 LightFM은 학습하지 않은 기본 모델이 아니다. 사용자 ID와 영화 ID를 feature로 사용하고, 앞 70% 평점에서 만든 긍정·부정 label을 logistic loss로 학습한 협업 모델이다. 이번 200명은 평균 행동 수가 많으므로 identity 기반 협업 신호만으로도 개인별 선호 순서를 강하게 학습할 수 있다.

현재 평가는 각 사용자가 실제로 평가한 뒤 30% 영화만 후보로 제공한다. 따라서 다음 V3 이점은 점수에 포함되지 않는다.

- 전체 카탈로그에서 관련 영화를 찾아오는 ontology 후보 검색
- 행동이 적거나 없는 사용자의 cold-start 대응
- metadata를 이용한 interaction 없는 영화 추천
- 단기 취향, OTT, 반복 감점과 서비스 정책

반면 V3의 시간 감쇠, 인기·활동량 보정, 사용자 semantic과 serving 점수 조정은 모두 이 평가에 반영된다. P0에서 A로 갈 때 발생한 약 `-0.026` 손실을 영화 ontology가 약 `+0.010` 회복했지만 전부 상쇄하지 못해 최종 V3가 더 낮아졌다.

따라서 단순 모델이 본질적으로 더 우수하다는 결론은 아니다. 현재 V3가 장기 취향 순위 판별이라는 한정된 목표에서 추가 복잡성의 이득을 증명하지 못했다는 뜻이다.

결과:

- `tests/v3_long_term_eval/outputs/matched_profile_recheck/TUNE-00/20260910T155012Z_TUNE-00_components_seed42/`

### Loss와 PASS 분리

ontology와 serving 보정을 제외하고 같은 V3 행동 입력에서 비교했다.

| 변형 | NDCG@20 | NDCG@30 |
| --- | ---: | ---: |
| WARP, PASS 제외 | 0.434003 | 0.471241 |
| Logistic, PASS 제외 | 0.447690 | 0.486071 |
| Logistic, PASS 음수 포함 | 0.449749 | 0.488047 |

판단:

- WARP에서 logistic으로 바꾸면 NDCG@20 `+0.013687`, NDCG@30 `+0.014830`이었다.
- logistic에서 PASS 음수를 추가하면 NDCG@20 `+0.002059`, NDCG@30 `+0.001977`이었다.
- 과거 집중형 100명에서 WARP가 이겼던 결과가 재현되지 않았다.
- 단일 seed 결과이므로 logistic을 즉시 운영 기본값으로 채택하지 않는다.
- WARP 선택 상태를 해제하고 다중 seed 재검증 대상으로 되돌린다.

결과:

- `tests/v3_long_term_eval/outputs/matched_profile_recheck/TUNE-02/20260910T160307Z_matched_seed42/`

### Loss와 PASS 다중 seed 확정

item-frequency 보정 `none`을 공통 조건으로 고정하고 matched 200명을 seed `42/43/44`에서 사용자별 `NDCG@20%`로 다시 평가했다. 동적 K는 `ceil(holdout 수 * 0.20)`이며 `7~222`, 평균 `30.205`였다.

| 변형 | 평균 NDCG@20% |
| --- | ---: |
| WARP, PASS 제외 | 0.437791 |
| Logistic, PASS 제외 | 0.439363 |
| Logistic, PASS 음수 포함 | 0.443258 |

판단:

- `Logistic + PASS 음수`가 세 seed에서 모두 1위였다.
- WARP 대비 평균 차이는 NDCG@20% `+0.005467`이었다.
- Logistic에서 PASS 음수를 추가한 효과도 평균 `+0.003895`로 세 seed 모두 양수였다.
- 안정 집중형 평균은 WARP `0.464899`에서 Logistic+PASS `0.469445`로 상승했다.
- 안정 다취향형 평균은 WARP `0.410683`에서 Logistic+PASS `0.417071`로 상승했다.
- WARP 대비 Logistic+PASS는 600개 사용자-seed 쌍 중 322개 상승, 270개 하락, 8개 동일이었다.
- R1 조건을 충족했으므로 이후 shadow 튜닝의 loss와 PASS 처리는 `logistic + 명시적 PASS 음수`로 고정한다.
- 운영 모델과 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/loss_pass_multiseed_20pct/TUNE-02/20260911T073536Z_matched_seed42/`
- `tests/v3_long_term_eval/outputs/loss_pass_multiseed_20pct/TUNE-02/20260911T073552Z_matched_seed43/`
- `tests/v3_long_term_eval/outputs/loss_pass_multiseed_20pct/TUNE-02/20260911T073607Z_matched_seed44/`

### 학습 보정 분해

현재 WARP hybrid 원점수는 NDCG@20 `0.441934`, NDCG@30 `0.482635`였다.

| 변경 | NDCG@20 | NDCG@30 | 판단 |
| --- | ---: | ---: | --- |
| 시간 감쇠 제거 | 0.441871 | 0.479061 | WARP에서는 이득 없음 |
| item-frequency 보정 제거 | 0.448270 | 0.484167 | @20 손실 가능성 확인 |
| user-activity 보정 제거 | 0.443805 | 0.480654 | 혼합·중립 |
| 두 표본 보정 제거 | 0.448137 | 0.483242 | item-frequency 영향이 더 큼 |

Logistic + PASS hybrid에서는 현재 감쇠와 표본 보정을 모두 유지한 값이 NDCG@20 `0.418940`, NDCG@30 `0.463804`였다. 시간 감쇠와 두 표본 보정을 제거하면 `0.457846`, `0.500112`까지 회복됐다.

이는 loss 하나만 바꿀 수 없고, loss와 시간 감쇠·표본 보정·ontology feature의 결합을 다시 맞춰야 한다는 증거다.

결과:

- `tests/v3_long_term_eval/outputs/matched_profile_recheck/DIAG-01/20260911_matched_200/`

## 4. 기존 실험 중 유지하는 결정

### 희소 metadata와 영화 feature 표현

다음 변경은 NDCG 최적화가 아니라 잘못된 점수 확대를 막기 위한 구조적 수정이므로 유지한다.

- 영화 전체 feature의 L1 상향 정규화 제거
- genre, keyword, actor, director, theme, mood field별 최대 budget
- 비어 있는 field의 budget 미재분배
- keyword IDF와 범용 feature 감쇄
- 정보가 적은 영화의 단일 feature 점수 독점 방지
- 상호작용이 없고 신작도 아닌 저신뢰 cold item의 초기 후보 제한

### 사용자 장기 semantic 표현 U2

기존 전체 L1은 장기 근거가 적은 사용자도 semantic 총량을 강제로 키웠다. 이를 다음 방식으로 바꾼 결정은 유지한다.

```text
long_term_semantic
  = item field-budget feature의 행동 가중 평균
  * positive_movie_count / (positive_movie_count + 5)
```

온보딩-only L1은 별도로 유지하고, 장기 행동 벡터는 합이 작다고 확대하지 않는다. 최신 재검사에서 사용자 ontology가 뚜렷한 이득을 만들지는 못했지만, 과거의 잘못된 상향 정규화로 되돌릴 이유는 없다.

### 영화 ontology 구조

- genre는 가장 일관된 핵심 family였다.
- director 제거는 seed별·cutoff별 방향이 엇갈려 전역 제거 근거가 없었다.
- 현재 여섯 family를 유지한다.
- 기존 field budget 대안들은 현재값보다 일관되게 낫지 않았다.

이 결정은 현재 구조를 유지한다는 뜻이다. 선택된 새 loss에서도 영화 ontology가 손실이면 family 실험 전체를 반복하기 전에 semantic scale부터 확인한다.

### 추천 안전성과 파이프라인

다음은 장기 NDCG와 별개로 유지한다.

- passed hard exclusion과 부정 취향 정책
- 초기 catalog trust filter와 최종 요청 조건 filter 분리
- 온톨로지 점수 이중 반영 제거
- LightFM 점수, ontology 점수, 정책 효과, 설명 근거 분리
- 협업 신뢰도가 LightFM 전체 후보 lane이 아니라 user-identity 성분에만 적용되는 구조

## 5. 다시 선택해야 하는 결정

| 항목 | 과거 판단 | 현재 상태 | 이유 |
| --- | --- | --- | --- |
| 사용자 ontology 범위·크기 | U2 전체 profile, semantic 0.5 | 장기 LightFM은 identity-only | family와 조합 모두 일관성 gate 실패 |
| collaborative confidence | 현재 방식 유지 | dense known user에는 미적용 | 사용자 간 후보 과집중 악화 |
| centering | 0.5 | 장기 취향 모델에서는 0 | 높일수록 NDCG 손실 증가 |

## 6. 인기 처리 책임 분리

2026-09-11부터 새 LightFM 학습의 `item_frequency_weighting` 기본값을 `inverse_sqrt`에서 `none`으로 변경했다. 인기 영화라는 이유만으로 사용자의 positive 상호작용을 학습 단계에서 약화하지 않는다.

matched 200명 seed 42 shadow 비교:

| 항목 | 기존 `inverse_sqrt` | 새 `none` | 변화 |
| --- | ---: | ---: | ---: |
| identity-only NDCG@20 | 0.434003 | 0.444971 | +0.010968 |
| identity-only NDCG@30 | 0.471241 | 0.481983 | +0.010742 |
| 최종 V3 NDCG@20 | 0.439608 | 0.442700 | +0.003092 |
| 최종 V3 NDCG@30 | 0.478932 | 0.480212 | +0.001280 |
| model top-100 Jaccard | 0.5149 | 0.6107 | +0.0958 |
| model top-20 Jaccard | 0.3918 | 0.4732 | +0.0814 |
| model top-20 고유 비율 | 0.3375 | 0.2875 | -0.0500 |

개인 선호 순위는 개선됐지만 모델 원본 후보의 사용자 간 중복은 증가했다. 이 trade-off 때문에 새 모델을 활성화하지 않았다.

정책 엔진에는 절대 최소 popularity와 후보 내 상위 percentile을 모두 충족한 인기 그룹이 허용 비율을 초과할 때만 적용되는 bounded popularity pressure를 추가했다. 첫 인기 영화는 허용하며, 인기 후보가 연속으로 과다 선택될 때만 점진적으로 순서를 낮춘다. 현재 기본 penalty는 `0`으로 비활성화되어 있으며 최종 model·ontology 구성이 확정된 뒤 R6에서 강도를 선택한다.

결과:

- shadow artifact: `tests/v3_long_term_eval/generated/matched_profiles/models_no_frequency/hybrid-23172632b1b9-06d36ab5c890-51c5cf5ab1c1-68b50bb09d72-846ee136c114-7b869d3b/`
- NDCG 분해: `tests/v3_long_term_eval/outputs/popularity_responsibility_split/TUNE-00/20260911T054843Z_TUNE-00_components_seed42/`

## 7. 종료된 실험 요약

다음 실험은 결과를 다시 선택하는 작업 목록에 넣지 않는다.

| 과거 실험 | 남은 판단 |
| --- | --- |
| TUNE-01 사용자 semantic 정규화 | U2 구조 유지, profile 내용과 크기만 후속 확인 |
| TUNE-03 SAVED/PINNED | WARP 기준 과도한 SAVED 강화는 일반화되지 않았음 |
| DIAG-02 영화 family 분해 | genre 핵심, 다른 family 전역 삭제 근거 없음 |
| TUNE-06 director 제거 | 제거하지 않음 |
| TUNE-07 field budget | 현재 budget 유지 |

과거 상세 산출물은 `tests/v3_long_term_eval/outputs/tuning/`에 보존한다. 이 문서에는 현재 의사결정에 필요한 결론만 유지한다.

## 8. 운영 상태

이번 matched 재검사는 shadow model과 테스트 전용 평가기로 수행했다.

- 활성 모델 변경 없음
- candidate snapshot 변경 없음
- serving bundle 변경 없음
- DB와 Redis 유지

다음 운영 반영은 학습 목표, 보정, ontology 결합, 모델 용량과 serving 보정을 모두 선택하고 미사용 사용자 검증을 통과한 뒤 한 번만 수행한다.

## 9. TUNE-24 최종 온톨로지 점수 결합

고정 V3 LightFM core에 현재 정책의 온톨로지 25%를 별도 결합한 효과를 개발 집단 A/B에서 측정했다. 모델-only 값 `0.878609`, `0.882331`은 TUNE-21/22의 동일 조합과 일치해 새 평가 경로가 기존 기준점을 재현했다.

| 집단 | model-only NDCG@100% | 현재 75/25 | 변화 |
| --- | ---: | ---: | ---: |
| 개발 A | 0.878609 | 0.877096 | -0.001513 |
| 개발 B | 0.882331 | 0.881487 | -0.000844 |
| 전체 | 0.880470 | 0.879292 | -0.001178 |

- seed별 변화: `-0.001037/-0.000697/-0.001801`
- 안정 집중형: `+0.000488`
- 안정 다취향형: `-0.002844`
- 사용자 방향: 188명 상승, 211명 하락, 1명 동일
- paired-user bootstrap 95% CI: `[-0.003630, +0.001167]`
- NDCG@20% 전체 변화: `+0.000404`, 집단별 방향은 서로 다름

근거 진단:

- train positive 영화 그래프 커버리지: 두 집단 모두 100%
- holdout 온톨로지 match rate: A 99.65%, B 99.33%
- 모델/온톨로지 순위 평균 상관: A 약 0.012, B 약 0.004
- 온톨로지 점수 비중: genre 약 58.5%, theme 약 21.8%, mood 약 13.7%, 나머지 약 6%

판단은 `gate 실패`다. 온톨로지가 근거를 못 찾은 문제가 아니라 거의 모든 후보와 연결되면서 구분력이 약한데도, percentile 정규화 후 25%를 고정 부여한 것이 핵심 의심 지점이다. 현재 25%를 장기 established user의 기본값으로 채택하지 않되, 콜드스타트·희소 사용자와 ontology candidate lane을 제거하는 근거로 확대 해석하지 않는다.

결과:

- `tests/v3_long_term_eval/outputs/ontology_fusion_ndcg100/TUNE-24/20260914T000241Z_development-a-b_seeds-42-43-44/`

## 10. TUNE-25 프로필 범위·정규화 분리

TUNE-24 손실의 원인을 확인하기 위해 LightFM과 온톨로지 비율은 `75/25`로 고정하고 프로필 범위와 온톨로지 정규화만 교차 비교했다.

| 변경 | NDCG@100% 변화 | 95% 신뢰구간 | 판단 |
| --- | ---: | --- | --- |
| 현재 프로필에서 percentile → bounded raw | +0.001407 | [+0.000168, +0.002665] | 일관된 개선 |
| percentile에서 반복 근거 프로필 적용 | -0.000009 | [-0.000053, +0.000026] | 영향 없음 |
| bounded raw에서 반복 근거 프로필 적용 | -0.000020 | [-0.000054, -0.000001] | 수치상 미세 손실 |
| 현재 프로필+bounded raw vs model-only | +0.000229 | [-0.001286, +0.001691] | 이득 불확실 |

현재 프로필+bounded raw의 model-only 대비 집단별 변화는 개발 A `+0.000167`, 개발 B `+0.000291`이었다. 그러나 seed 44는 `-0.000520`, 안정 다취향형은 `-0.001412`여서 최종 채택 gate를 통과하지 못했다.

반복 근거 프로필이 제거한 특징은 사용자당 평균 1개 안팎이었다. 후보 match rate는 A `99.65%`, B `99.33%`로 그대로였으며 raw score 범위와 family 비중도 거의 같았다. 따라서 현재 문제를 단발성 actor/keyword가 많이 포함된 탓으로 볼 근거는 없었다.

결론:

- 후보 내부의 온톨로지 점수를 매번 0~1 전체 범위로 확대하는 percentile 정규화가 확인된 손실 원인이다.
- field budget으로 이미 상한이 있는 원점수를 유지하는 bounded raw가 다음 진단 기준이다.
- 프로필 반복 근거 필터는 채택하지 않는다.
- bounded raw는 기존 손실을 제거했지만 model-only보다 명확히 낫지는 않으므로 아직 운영 반영하지 않는다.
- 다음 진단은 bounded raw 상태에서 ontology family별 순위 기여를 분리한다.

결과:

- `tests/v3_long_term_eval/outputs/ontology_fusion_ndcg100/TUNE-25/20260914T011327Z_development-a-b_seeds-42-43-44/`

## 11. TUNE-26 bounded raw family 기여 분해

현재 프로필과 `bounded_raw`, LightFM/온톨로지 `75/25`를 고정한 뒤 여섯 ontology family를 하나씩 제외했다. 제거한 family의 budget은 다른 곳에 재분배하지 않았다.

| 제외 family | 제외 - 전체 NDCG@100% | seed 방향 | 사용자 유형 방향 | 판단 |
| --- | ---: | --- | --- | --- |
| genre | -0.000074 | 2개 음수, 1개 양수 | 서로 반대 | 불확실 |
| keyword | +0.000083 | 2개 양수, 1개 음수 | 서로 반대 | 불확실 |
| actor | -0.000019 | 혼합 | 모두 미세 음수 | 사실상 중립 |
| director | -0.000705 | 모두 음수 | 모두 음수 | 유용한 신호 |
| theme | +0.000135 | 혼합 | 서로 반대 | 불확실 |
| mood | +0.000263 | 모두 양수 | 다취향 양수, 집중형 미세 음수 | 제거 후보이나 미확정 |

`director` 제거 효과의 95% 신뢰구간은 `[-0.001331, -0.000076]`이었다. 점수 비중은 A `2.35%`, B `2.32%`로 작지만 실제 순위 구분에는 도움이 됐으므로 유지한다. `mood` 제거의 신뢰구간은 `[-0.000012, +0.000544]`로 0을 포함해 전역 삭제 기준을 통과하지 못했다.

전체 bounded 결합의 model-only 대비 변화는 집중형 `+0.001870`, 다취향형 `-0.001412`였다. family 하나를 제외한 모든 변형에서도 다취향형은 model-only보다 낮았다. 특정 family 하나를 지우는 방식으로는 사용자 유형 간 손익 차이가 해결되지 않는다.

결론:

- 여섯 family를 유지하며 `director`는 삭제하지 않는다.
- 현재 문제를 family 전역 삭제 문제로 보지 않는다.
- 다음에는 서비스에서 계산 가능한 취향 집중도가 집중형·다취향형 손익 차이를 설명하는지 진단한다.
- 조건부 결합의 근거가 확인되기 전에는 ontology 비율을 사용자 유형별로 변경하지 않는다.
- 운영 모델과 활성 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/ontology_fusion_ndcg100/TUNE-26/20260914T210308Z_development-a-b_seeds-42-43-44/`

## 12. TUNE-27 취향 집중도 진단

최종 온톨로지 점수가 집중형에서 이득이고 다취향형에서 손실인 이유를 확인하기 위해, 70% 학습 구간 positive 영화의 정규화 장르 엔트로피와 사용자별 온톨로지 결합 효과를 비교했다. holdout 정답은 집중도 계산에 사용하지 않았다.

- 엔트로피와 온톨로지 효과 Pearson 상관: `-0.106202`
- Spearman 상관: `-0.143869`
- 개발 A/B Pearson 상관: `-0.142747/-0.066754`
- seed 42/43/44 Pearson 상관: `-0.124817/-0.083050/-0.086551`
- positive 행동 수와 효과 상관: `+0.005362`

| 엔트로피 사분위 | 사용자 | 평균 효과 | 상승/하락 |
| --- | ---: | ---: | --- |
| Q1 | 100 | +0.002128 | 58/41 |
| Q2 | 100 | +0.001611 | 57/42 |
| Q3 | 100 | -0.001090 | 51/49 |
| Q4 | 100 | -0.001733 | 43/57 |

낮은 엔트로피에서 이득이고 높은 엔트로피에서 손실인 방향은 두 cohort와 세 seed에서 재현됐고 사분위 평균도 단조롭게 감소했다. 따라서 취향 집중도는 조건부 최종 온톨로지 결합을 검토할 근거가 된다. 하지만 상관은 약하고 각 구간 내부에도 상승·하락 사용자가 모두 많아 개인별 손익 예측기로 확정할 수 없다.

집중형 표본의 최대 엔트로피는 `0.802862`, 다취향형 최소는 `0.864500`으로 중간 `0.061638` 구간에 사용자가 없다. 운영 임계값은 선택하지 않았다. 다음에는 기존 사용자를 제외한 연속 엔트로피 개발 집단으로 중간 구간의 손익 전환을 확인해야 한다.

운영 모델과 활성 bundle은 변경하지 않았다.

결과:

- `tests/v3_long_term_eval/outputs/ontology_fusion_ndcg100/TUNE-27/20260914T211737Z_development-a-b_seeds-42-43-44/`

## 13. TUNE-28 연속 엔트로피 사용자 재검증

TUNE-27 표본의 중간 엔트로피 공백을 제거하기 위해 이전 평가 사용자 1,000명과 겹치지 않는 새 사용자 600명을 선발했다. 각 개발 집단은 낮음·중간·높음 100명씩이며, 한 집단 안에서 세 구간의 대표 장르 분포는 동일하다. 매칭된 세 사용자의 리뷰 수 평균 상대 차이는 `1.64%`, 최대는 `19.87%`였다.

현재 프로필 `bounded_raw 75/25`를 V3 core model-only와 비교한 결과는 다음과 같다.

- 전체 NDCG@100% 변화: `+0.001393`
- paired-user bootstrap 95% CI: `[+0.000301, +0.002468]`
- seed 변화: `+0.001648/+0.001283/+0.001248`
- 개발 A/B: `+0.001176/+0.001611`
- 낮음/중간/높음 엔트로피: `+0.001944/+0.001703/+0.000533`
- 사용자 방향: 344명 상승, 254명 하락, 2명 동일

엔트로피와 온톨로지 효과의 Pearson/Spearman 상관은 `-0.057448/-0.029360`으로 매우 약했다. 사분위 효과도 `+0.001998/+0.001944/+0.000776/+0.000855`로 모두 양수였다. 높은 엔트로피에서 온톨로지가 손실이라는 TUNE-27 가설은 연속 분포에서 재현되지 않았다.

따라서 취향 집중도에 따른 조건부 온톨로지 비율은 기각한다. 대신 현재 프로필과 bounded raw `75/25`를 고정된 최종 검증 후보로 남긴다. TUNE-28은 개발 검증이므로 활성 모델과 bundle은 변경하지 않았다.

결과:

- 사용자 선발: `tests/v3_long_term_eval/generated/continuous_entropy_profiles/`
- 진단: `tests/v3_long_term_eval/outputs/ontology_fusion_ndcg100/TUNE-28/20260914T212745Z_development-a-b_seeds-42-43-44/`

## 14. TUNE-29 순수 LightFM 대비 최종 일반화

기존 TUNE-21~28 사용자 1,600명을 모두 제외하고 새 사용자 600명을 선발했다. 두 집단은 각각 낮음·중간·높은 장르 엔트로피 사용자 100명씩이며, 같은 대표 장르와 비슷한 리뷰 수로 매칭했다. 설정 선택 없이 순수 LightFM, 고정 V3 core, bounded ontology를 결합한 V3를 동일한 정답지와 seed `42/43/44`에서 비교했다.

| Endpoint | NDCG@100% | NDCG@20% |
| --- | ---: | ---: |
| 순수 LightFM | 0.874384 | 0.647011 |
| 고정 V3 LightFM core | 0.878370 | 0.658996 |
| V3 core + bounded ontology 75/25 | 0.879421 | 0.661583 |

순수 LightFM 대비 V3 core 변화는 `+0.003986`, bounded ontology의 추가 변화는 `+0.001050`, 최종 V3의 전체 변화는 `+0.005036`이었다. 최종 V3 비교의 paired-user bootstrap 95% 신뢰구간은 `[+0.003240, +0.006849]`였고 두 집단, 세 seed와 세 엔트로피 구간에서 모두 양수였다.

세부 결과:

- V3 core 대비 ontology: 311명 상승, 288명 하락, 1명 동일
- 순수 LightFM 대비 최종 V3: 351명 상승, 248명 하락, 1명 동일
- 순수 대비 최종 V3 엔트로피 구간별 변화: 낮음 `+0.007975`, 중간 `+0.003945`, 높음 `+0.003189`
- 순수 대비 최종 V3 집단별 변화: final A `+0.004254`, final B `+0.005818`

판단은 세 endpoint 비교 gate 통과다. 장기 취향의 주어진 holdout 영화 순위에서는 V3 core와 bounded ontology가 각각 독립적인 추가 이득을 냈다. 하지만 전체 카탈로그 후보 검색과 serving 정책을 제외한 평가이므로 추천 후보를 잘 찾아내는 능력까지 입증한 것은 아니다. 운영 모델과 활성 bundle은 변경하지 않았다.

결과:

- 사용자 선발: `tests/v3_long_term_eval/generated/final_ontology_generalization_profiles/`
- 비교: `tests/v3_long_term_eval/outputs/final_generalization_ndcg100/TUNE-29/20260914T215204Z_seeds-42-43-44/`
