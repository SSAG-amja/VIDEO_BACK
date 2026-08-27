# 04. LightFM 가중치 및 조정 지점

## 1. 문서 책임

이 문서는 V3에서 조정 가능한 숫자를 다음 세 종류로 분리한다.

```text
1. 행동 sample weight
   어떤 positive 행동을 얼마나 강하게 학습할 것인가

2. LightFM hyperparameter
   latent model을 어떤 크기와 규제로 학습할 것인가

3. 추천 결합 weight
   LightFM, 단기 취향, ontology, policy를 최종 순위에 어떻게 반영할 것인가
```

세 종류의 숫자를 한 번에 변경하지 않는다. 원인을 구분할 수 없기 때문이다.

## 2. 행동 weight 범위

### 2.1 현재 구현된 직접 행동

위치: `app/services/recsys/v3/config.py`

| 행동 | 현재 값 | 역할 | 상태 |
| --- | ---: | --- | --- |
| favorite | `1.0` | timestamp 없는 onboarding prior | provisional |
| watched | `1.5` | 긍정 취향이지만 소비 완료 | provisional |
| saved | `2.0` | 강한 명시적 positive | provisional |
| pinned | `2.0` | 강한 명시적 positive | provisional |

이 값은 V1의 `+4`, `+6` 같은 추천 점수를 복사한 것이 아니다. LightFM의 positive pair별 sample weight다.

`passed`는 WARP sample weight가 없다. WARP positive에서 제외하고 serving hard filter와 ontology negative policy로 처리한다.

현재 dataset builder는 위 네 positive와 passed만 학습 행렬에 반영한다. 게시글·좋아요·댓글 raw signal 추출과 provenance는 구현됐지만 아래 weight, eligibility, 집계식은 아직 config와 positive builder에 연결되지 않았다.

### 2.2 추가할 게시글·소셜 행동

| 행동 | 최초 weight/계수 후보 | 시간 처리 | 상태 |
| --- | ---: | --- | --- |
| movie_post_write | `2.2` | `posts.created_at` recency | provisional |
| movie_post_like | `0.45` | 현재 시점 build에서만 missing-time multiplier 적용 | provisional |
| movie_post_reply | `0.55` | `replies.created_at` recency | provisional |
| playlist_post_write | movie post weight의 `0.5` | post 시점 구성에 `1/N` 분산 | provisional |
| playlist_post_reply | movie reply weight의 `0.5` | reply 시점 구성에 `1/N` 분산 | provisional |
| playlist_post_like | 없음 | 좋아요 시점 복원 불가 | deferred |

초기값은 V1의 상대 순서인 `post_write > playlist_add > reply > like`를 참고하되 V1 추천 점수를 그대로 복사하지 않는다. 영화 게시글 작성 외의 social 행동은 의미가 모호하므로 saved/pinned보다 낮게 시작한다.

추가할 config 후보:

```text
TRAINING_SOCIAL_ACTION_WEIGHTS
TRAINING_PLAYLIST_DERIVED_MULTIPLIER = 0.5
TRAINING_SOCIAL_ONLY_MAX_WEIGHT = 0.8
TRAINING_SOCIAL_BONUS_CAP = 0.25
TRAINING_SOCIAL_MISSING_TIMESTAMP_MULTIPLIER = 0.5
```

이름과 값은 builder 구현 전에 확정하고 artifact manifest에 저장한다.

## 3. 중복 행동 집계

현재 구현은 같은 사용자-영화에 여러 직접 positive 상태가 겹칠 때 다음처럼 계산한다.

```text
effective_action_weight
  = base_action_weight * recency_multiplier

sample_weight
  = strongest effective_action_weight
  + sum(other_action_recency * overlap_bonus)

sample_weight
  = min(sample_weight, max_sample_weight)
```

현재 값:

| 설정 | 값 | 의미 |
| --- | ---: | --- |
| `TRAINING_OVERLAP_CONFIDENCE_BONUS` | `0.15` | 다른 positive 종류가 겹칠 때의 작은 신뢰도 증가 |
| `TRAINING_MAX_SAMPLE_WEIGHT` | `2.3` | 한 user-movie pair의 영향 상한 |

예:

```text
최근 saved       2.0 * 1.0 = 2.0
100일 전 watched 1.5 * 0.6 = 0.9
overlap bonus    0.15 * 0.6 = 0.09

최종 sample weight = 2.09
```

반복 이벤트 횟수는 현재 snapshot schema에 없으므로 추정하거나 추가 합산하지 않는다.

게시글·social 신호를 추가한 목표 계산은 직접 신호와 파생 신호를 분리한다.

```text
primary_component
  = strongest(favorite, watched, saved, pinned, movie_post_write)

primary_overlap
  = bounded bonus from other primary actions

derived_units
  movie target event    = 1
  playlist target event = 1 / valid_movie_count_at_event_time

derived_component
  = action_weight * log2(1 + accumulated_units)

if primary exists:
  sample_weight = primary_component
                + primary_overlap
                + min(sum(derived_component), social_bonus_cap)
else:
  sample_weight = min(sum(derived_component), social_only_max_weight)

sample_weight = min(sample_weight, global_max_sample_weight)
```

이 구조는 게시글을 반복 작성하거나 큰 플레이리스트에 반응한 사용자가 한 영화에 saved/pinned한 사용자보다 큰 sample weight를 만드는 것을 막는다.

## 4. 최근성 multiplier

현재 값은 V1의 해석 가능한 bucket을 초기값으로 사용한다.

| 행동 나이 | multiplier |
| --- | ---: |
| 0~30일 | `1.0` |
| 31~90일 | `0.8` |
| 91~180일 | `0.6` |
| 181일 이상 | `0.4` |
| timestamp 없는 onboarding favorite | `1.0` |
| timestamp 없는 social signal | 현재 시점 build만 목표값 `0.5` |

timestamp가 없는 favorite를 오래된 행동으로 임의 감점하지 않는다.

조정 시 확인할 현상:

- 오래된 watched가 최신 saved보다 과도하게 강하면 decay를 더 빠르게 한다.
- 장기 사용자에서 유효 interaction이 지나치게 약해지면 older multiplier를 높인다.
- 단기 취향과 학습 최근성이 같은 신호를 이중 증폭하면 sample decay 또는 drift 중 하나의 상한을 낮춘다.
- 오래된 좋아요의 시점을 알 수 없는 현재 schema에서는 movie post like의 base weight와 missing-time multiplier를 동시에 높이지 않는다.

## 5. 행동 weight 조정 순서

한 번에 하나의 가설만 적용한다.

권장 순서:

```text
1. favorite 대비 saved/pinned 상대 강도
2. watched와 movie post write 상대 weight
3. social-only weight와 playlist 분산 계수
4. overlap/social bonus와 상한
5. recency 및 missing-time multiplier
6. loss 변경
```

관측과 조정 예:

| 관측 | 우선 확인 | 가능한 조정 |
| --- | --- | --- |
| saved 영화과 관련된 후보 rank가 낮음 | saved signal 수, feature coverage | saved `2.0` 상향 또는 favorite 하향 |
| watched가 추천을 과도하게 지배 | 사용자별 watched 비율 | watched `1.5` 하향 |
| onboarding favorite만 있는 사용자가 불안정 | favorite coverage | favorite 유지, user feature/cold-start 보완 |
| 여러 행동이 겹친 영화만 과도하게 지배 | overlap pair 비율 | bonus `0.15` 또는 cap `2.3` 하향 |
| 최근 취향 반영이 약함 | sample decay와 short-term source 분리 확인 | 학습 weight보다 short-term retrieval 우선 조정 |
| 게시글 활동이 많은 사용자가 순위를 지배 | source별 pair와 cap 도달률 | social cap 또는 log saturation 강화 |
| 큰 플레이리스트 영화가 과대 반영 | event당 unit 합과 playlist 크기 | `1/N` 투영 및 derived multiplier 확인 |
| social-only 사용자의 결과가 불안정 | movie post like/reply coverage | social-only cap 조정 전 신호 방향성부터 확인 |

최근 취향 문제를 행동 weight만 높여 해결하지 않는다. 단기 후보는 별도 source다.

## 6. LightFM 모델 hyperparameter

identity-only trainer에는 아래 시작값이 구현되어 있다. 실제 행동 데이터 기준선 비교 전까지 확정값으로 간주하지 않는다.

| parameter | 첫 기준 후보 | 조정 방향 |
| --- | --- | --- |
| `loss` | `warp` | top-K implicit ranking 기준 |
| `no_components` | `64` | `32 / 64 / 128` 비교 |
| `epochs` | `40` | underfit이면 증가, 과적합·시간 증가 시 감소 |
| `learning_rate` | `0.05` | 불안정하면 `0.02~0.03`, 느리면 신중히 증가 |
| `user_alpha` | `1e-6` | user embedding 과적합 시 증가 |
| `item_alpha` | `1e-6` | item/feature embedding 과적합 시 증가 |
| `max_sampled` | `10` | WARP negative sampling 강도와 시간 조정 |
| `random_state` | `42` | build 비교 시 고정 |
| `num_threads` | 실행 환경 기준 | 결과 재현성과 처리시간을 함께 기록 |

기본값은 `config.py`에 정의하고 실행별 `LightFMTrainingConfig`와 artifact manifest에 실제 사용값을 저장한다.

### 조정 의미

`no_components`:

- 너무 작으면 다양한 협업 패턴을 표현하지 못한다.
- 너무 크면 희소 사용자·영화 embedding이 과적합하고 artifact가 커진다.

`user_alpha`, `item_alpha`:

- 값이 크면 embedding을 더 강하게 규제한다.
- feature 수가 많은 hybrid 모델에서는 identity-only와 같은 alpha가 최적이라는 보장이 없다.

`epochs`:

- 데이터 크기와 loss 변화 추이를 함께 본다.
- 고정 epoch 증가를 품질 개선으로 간주하지 않는다.

`max_sampled`:

- 큰 값은 WARP가 어려운 negative를 더 찾게 하지만 학습 시간이 증가한다.

## 7. Loss 선택

기본:

```text
WARP
  positive implicit feedback의 top-K 순위 최적화
```

주의:

- passed를 `-1` interaction으로 WARP에 넣지 않는다.
- BPR은 ranking 대안이지만 같은 sample weight 의미를 자동 보장하지 않는다.
- logistic loss에서 명시적 negative를 실험하려면 WARP용 행렬을 그대로 재사용하지 않는다.
- loss가 달라지면 model build 이름과 config를 분리한다.

## 8. Ontology feature 조정 지점

LightFM에는 graph 전체가 아니라 sparse feature를 전달한다.

namespace:

```text
genre:{id}
keyword:{id}
actor:{person_id}
director:{person_id}
theme:{key}
mood:{key}
```

주요 조정값:

| 항목 | 최초 방향 | 이유 |
| --- | --- | --- |
| actor 최소 movie frequency | `>= 5` | 1편 actor는 다른 영화 일반화 이득이 작음 |
| actor 비교 후보 | `>= 2 / 5 / 10` | cardinality와 coverage 균형 |
| keyword 최소 frequency | `>= 5` | 희소 noise 제거, provisional |
| keyword 최대 catalog 비율 | `<= 0.5` | 지나치게 흔한 feature 제거, provisional |
| relation feature weight | `edge.weight * confidence` 기반 | 원천 신뢰도 반영 |
| OTT model feature | 사용하지 않음 | 구독과 제공 여부는 취향이 아니라 최신 rule filter 입력 |

전체 graph에서 관계를 삭제하지 않고 LightFM exporter에서만 feature cardinality를 제한한다.

현재 exporter는 movie identity column을 항상 유지하고 factual edge는 `1.0`, semantic edge는 canonical `effective_strength`를 CSR 값으로 사용한다. threshold와 ratio는 export manifest에 저장하며 전체 graph 실측과 ablation 전에는 확정값으로 간주하지 않는다.

## 9. User feature 조정 지점

기본 필수:

- user identity
- onboarding genre
- onboarding favorite에서 추출한 feature

선택 후보:

- keyword
- actor/director
- theme/mood

subscribed OTT는 user feature 후보가 아니다. 별도 serving context에서 최신 availability filter와 정책에만 전달한다.

행동 interaction이 이미 표현하는 동적 취향을 user feature에 그대로 중복하지 않는다. user feature는 cold-start와 안정적인 prior 중심으로 구성한다.

현재 S5 구현값:

```text
user identity                     1.0
explicit onboarding genre         1.0
favorite-derived retained feature item feature value * 0.5
동일 user-token 중복               max
vocabulary                        전체 retained genre + 현재 favorite에서 관측된 token
OTT                               제외
```

전체 item feature 150만 개를 user vocabulary로 그대로 복제하면 user feature embedding 메모리가 불필요하게 커진다. 따라서 genre는 신규 사용자 feature-only 경로를 위해 전체 retained vocabulary를 유지하고 keyword/actor/director/theme/mood는 현재 onboarding favorite에서 관측된 token만 포함한다. 이 범위와 `0.5`는 실제 ablation 전 provisional이다.

## 10. 후보 결합 weight

이 값은 LightFM 학습 parameter가 아니라 retrieval/reranking 설정이다.

장기·단기 후보:

```text
candidate_selection_score
  = (1 - drift_weight) * normalized_long_term_score
  + drift_weight * normalized_short_term_score
```

초기 drift 범위:

| 최근 신호 | `drift_weight` 방향 |
| --- | ---: |
| 없음 | `0.00` |
| 최근 positive 영화 1개 | `0.10~0.20` |
| 다른 영화에서 같은 concept 반복 | `0.35~0.45` |
| 최대값 | `0.45` |

현재 S7 구현은 `drift_weight = drift_confidence * 0.45`를 사용한다. `drift_confidence >= 0.60`이면 contextual source floor를 활성화하며 최대 보장량은 최종 후보의 25%다. 이 값은 `app/services/recsys/v3/config.py`에 있고 실제 사용자 분포 평가 전 provisional이다.

개인 점수·온톨로지:

```text
base_score
  = 0.75 * normalized_personal_score
  + 0.25 * normalized_ontology_score
```

`0.75/0.25`는 구현 확정값이 아니라 최초 결합 후보다. LightFM 품질과 ontology evidence 품질을 분리해서 확인한 뒤 조정한다.

## 11. Policy adjustment 상한

다음 값은 LightFM을 재학습하지 않고 policy config에서 조정한다.

- OTT bonus
- quality bonus
- negative preference penalty
- repetition penalty
- MMR relevance/diversity balance

policy adjustment가 model/ontology base score를 완전히 뒤집지 않도록 유형별·총량 상한을 둔다. hard filter는 상한 대상이 아니다.

현재 S8 provisional 값은 `app/services/recsys/v3/config.py`에 있다.

```text
personal / ontology             0.75 / 0.25
short ontology multiplier       0.50
all-mode subscribed OTT bonus   최대 0.04
recent release bonus            최대 0.03, 365일 선형 감쇠
quality bonus                   최대 0.08
vote confidence prior           100
negative penalty                base의 30%, 절대 0.20 중 작은 값
negative confidence saturation  passed 3건
MMR similarity penalty          최대 0.08
repetition penalty              최대 0.06
cold-start feature-only 비중    0.65
```

품질 원점수는 `vote_count / (vote_count + 100)` 신뢰도를 rating과 bounded popularity 결합값에 먼저 곱한다. 따라서 popularity가 높아도 vote count가 1이면 품질 bonus는 작다. 최소 vote count hard filter는 사용하지 않는다.

negative feature 초기 상대값은 genre `0.15`, actor `0.30`, keyword `0.35`, mood `0.35`, director/theme `0.45`다. 최근 negative ontology score에는 `1.25` multiplier를 사용한다. 이는 V2 절대값 계승이 아니라 비교를 위한 초기값이다.

## 12. 조정 단위와 기록

한 model build에서 함께 고정할 값:

```text
행동 weight/recency
loss/hyperparameter
user/item feature registry
feature frequency threshold
dataset hash/cutoff
random seed/package versions
source별 projection rule과 social cap
```

serving bundle에서 고정할 값:

```text
model build
ontology build
source normalization
personal/ontology 결합 weight
policy config
```

추천 결과에는 다음을 남긴다.

```text
model raw/normalized score
ontology type score
policy별 effect
final score
각 build/config version
positive pair의 대표 action과 source provenance
```

## 13. 사람이 수행하는 tuning 절차

```text
분리 진단에서 이상 패턴 확인
-> 원인 계층 결정
   behavior weight | model | feature | ontology | policy
-> 한 종류의 값만 변경한 새 build/config 생성
-> 동일한 data cutoff와 mapping 조건에서 비교
-> 개선 근거와 부작용 기록
-> serving bundle 활성화 여부 결정
```

추천 이유를 보고 LightFM이 해당 feature 때문에 추천했다고 단정하지 않는다. ontology evidence는 tuning 가설을 만드는 관측 자료이고 LightFM의 인과 설명이 아니다.

## 14. 현재 확정되지 않은 값

- identity-only trainer hyperparameter의 실제 데이터 기준 적정값
- hybrid model의 feature별 scale
- keyword/actor frequency threshold 최종값
- user feature 범위
- drift confidence 계산식
- personal/ontology 결합 비율
- policy adjustment 상한
- MMR lambda
- 게시글·social 행동 weight와 상한
- timestamp가 없는 like의 multiplier
- reply를 positive로 유지할지 여부

이 값들은 구현 코드에 숨은 기본값으로 넣지 않고 config와 artifact에 명시한다.
