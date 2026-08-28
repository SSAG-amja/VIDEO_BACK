# 03. V3 추천 정책

## 1. 문서 책임

이 문서는 V3가 사용자 행동을 어떻게 해석하고 후보를 필터링·재정렬하는지 정의한다.

구체적인 숫자와 조정 위치는 [04 LightFM 조정 지점](04_lightfm_tuning.md)을 따른다.

피드 세션, 연속 페이지, 새로고침, 행동 반영 시점과 worker 게시 안전성은 점수 정책과 분리한다. 현재 미구현 상태와 결정 항목은 [08 후속 작업](08_additional_work_backlog.md) P0에 기록한다.

## 2. 정책 기준 우선순위

```text
V1
  행동 의미, exclusion, OTT, cold-start, worker 안정성의 기본 비교 기준

V2
  V1에 없는 ontology evidence, bounded semantic negative,
  exposure 관측, set-based graph query를 선택적으로 참고

V3 신규
  LightFM 학습, source 정규화, hybrid 후보,
  model/ontology/policy 점수 분리
```

V2가 더 최신이라는 이유만으로 채택하지 않는다. V2의 scorer/ranker 전체를 V3 정책으로 가져오지 않는다.

정책 결정은 `app/services/recsys/v3/policy/policy_registry.py`에 다음 상태로 기록한다.

```text
selected_source = v1 | v2 | v3_new
status = adopted | provisional | deferred
selection_reason
references
config_keys
comparison_required
```

## 3. 행동 신호 정책

현재 DB의 `saved`는 별도 테이블이 아니라 사용자가 자신의 플레이리스트에 영화를 추가한 `playlist_movies` 관계다. `likes`는 영화 자체의 좋아요가 아니라 게시글 좋아요다. 두 의미를 혼동하지 않는다.

### 3.1 직접 영화 행동

| 행동 | DB 원천 | LightFM | 단기 취향 | 동일 영화 재노출 | 의미 |
| --- | --- | --- | --- | --- | --- |
| favorite | `user_favorite_movies` | positive prior | 기본 prior | 허용 | onboarding에서 고른 초기 선호 |
| pinned | `user_interactions` | 강한 positive | 강한 positive | 허용 | 명시적 관심 |
| saved | `playlist_movies` + 소유 playlist | 강한 positive | 강한 positive | 허용 | 직접 분류·보관한 영화 |
| watched | `user_interactions` | positive | positive | 제외 | 소비 완료와 취향 신호 |
| passed | `user_interactions` | WARP positive 제외 | negative | 제외 | 명시적 거부 |

- 같은 영화를 여러 플레이리스트에 저장해도 하나의 user-movie pair로 합친다.
- 플레이리스트 개수나 현재 true 상태를 반복 행동 횟수로 해석하지 않는다.
- watched는 학습에는 positive지만 이미 본 동일 영화는 serving에서 제외한다.
- passed는 positive weight를 갖지 않으며 동일 영화 hard filter와 의미 기반 negative policy에 사용한다.

### 3.2 게시글·좋아요·댓글 신호

게시글 행동은 사용자가 영화에 직접 수행한 행동보다 의미가 약하거나 모호할 수 있으므로 provenance와 신뢰도 등급을 분리한다.

| 행동 | 사용자에서 영화로 투영 | 정책 | 상태 |
| --- | --- | --- | --- |
| 영화 게시글 작성 | 작성자 -> `posts.movie_id` | 영화가 명시된 게시 행동 신호 | provisional |
| 플레이리스트 게시글 작성 | 작성자 -> 게시 시점에 포함된 각 영화 | 전체 신호량을 `1/N`로 분산 | provisional |
| 영화 게시글 좋아요 | 좋아요 사용자 -> `posts.movie_id` | 글/작성자에 대한 반응일 수도 있어 약한 positive | provisional |
| 영화 게시글 댓글 | 댓글 작성자 -> `posts.movie_id` | 감정 방향을 모르므로 약한 engagement positive | provisional |
| 플레이리스트 게시글 댓글 | 댓글 작성자 -> 댓글 시점에 포함된 각 영화 | 약한 신호를 `1/N`로 분산 | provisional |
| 플레이리스트 게시글 좋아요 | 좋아요 사용자 -> 플레이리스트 영화 | 좋아요 시각과 당시 구성 복원이 불가능해 1차 학습에서 제외 | deferred |
| 내 게시글이 받은 좋아요·댓글 | 게시글 작성자에게 역으로 투영하지 않음 | 타인의 반응은 작성자의 추가 취향 행동이 아님 | excluded |
| hashtag·게시글 본문 | user-movie interaction으로 직접 변환하지 않음 | 별도 NLP/semantic 근거가 생기기 전 미사용 | deferred |

영화 게시글의 대상은 고정되어 있어 좋아요 시각이 없어도 대상 영화는 식별할 수 있다. 다만 `likes`에 `created_at`이 없으므로 최신성은 부여하지 않고 낮은 신뢰도로 제한한다. 또한 현재 시점 snapshot build에서만 사용하고, 과거 `data_cutoff_at`을 재현하는 build에서는 미래 정보 누출을 막기 위해 제외한다.

플레이리스트는 게시 후 영화가 추가·삭제될 수 있다. 따라서 작성과 댓글은 다음 시점 경계를 적용한다.

```text
playlist_post_write:
  playlist_movie.created_at <= post.created_at

playlist_post_reply:
  playlist_movie.created_at <= reply.created_at
```

삭제된 과거 구성은 현재 schema에서 복원할 수 없다. 완전한 이력이라고 주장하지 않고 diagnostics에 snapshot 한계를 기록한다.

### 3.3 중복·충돌·과대 반영 방지

- 학습 행렬에는 사용자-영화당 positive row를 하나만 만든다.
- passed와 어떤 positive가 충돌하면 passed가 우선하고 conflict count를 남긴다.
- 직접 행동 또는 영화 게시글 작성 중 기본 weight가 가장 높은 신호를 대표값으로 사용한다.
- 나머지 직접 행동은 bounded overlap bonus만 더한다.
- 좋아요·댓글 같은 간접 신호는 별도 social cap 안에서만 더한다.
- 간접 신호만 있는 pair도 학습 가능하지만 직접 saved/pinned보다 높은 weight를 가질 수 없다.
- 같은 대상에 게시글·댓글을 반복해도 선형 합산하지 않고 로그 포화와 pair 상한을 적용한다.
- self-like와 자기 게시글에 작성한 댓글은 게시글 작성 신호를 중복 강화하지 않는다.
- 플레이리스트 기반 한 번의 행동이 N개 영화에 N배로 증폭되지 않도록 영화당 `1/N` unit을 배분한다.

## 4. 협업 필터링 정책

### 4.1 학습 단위

LightFM의 협업 신호는 별도 수동 유사도 점수가 아니라 전체 사용자의 user-movie interaction matrix에서 학습한다.

```text
row    = user identity
column = movie identity
value  = implicit positive 1
weight = 행동 강도, 최근성, provenance 신뢰도를 합친 sample weight
```

같은 영화에 saved/pinned한 사용자, 같은 영화 게시글을 작성·좋아요한 사용자가 여러 영화에서 유사한 패턴을 보이면 latent factor가 이를 학습한다. 특정 게시글 작성자와 좋아요 사용자를 직접 user-user edge로 연결하거나 점수를 복사하지 않는다.

### 4.2 학습 단계

```text
identity-only baseline
  user identity + movie identity
  -> 순수 협업 신호의 기준선

hybrid model
  identity feature
  + 영화 ontology feature
  + 안정적인 onboarding user feature
  -> 협업 신호와 content 일반화를 함께 학습
```

identity interaction이 없는 신규 사용자는 기존 feature vocabulary로 표현될 때 feature-only LightFM 점수를 사용할 수 있다. 충분한 user feature도 없으면 rule-based cold-start로 전환한다.

### 4.3 Positive와 negative 의미

- WARP에서 관측되지 않은 user-movie pair는 명시적 싫어요가 아니라 sampled unobserved item이다.
- passed를 WARP의 음수값이나 positive interaction으로 넣지 않는다.
- passed 의미는 serving exclusion과 bounded ontology negative로 분리한다.
- 게시글 좋아요와 댓글은 콘텐츠 참여 신호이지 영화 만족도나 시청 완료 신호가 아니다.
- popularity, post like 수, 다른 사용자의 반응량을 특정 사용자의 positive로 변환하지 않는다.

### 4.4 시간과 데이터 경계

- model build는 하나의 `data_cutoff_at` 이전 신호만 읽는다.
- timestamp가 있는 행동은 cutoff와 recency를 적용한다.
- timestamp가 없는 favorite는 안정적인 onboarding prior로 취급한다.
- timestamp가 없는 게시글 좋아요는 현재 시점 build에서만 별도 낮은 missing-time multiplier를 사용한다.
- 과거 cutoff build에서는 발생 시점을 증명할 수 없는 게시글 좋아요를 제외한다.
- 현재 상태 snapshot에서 사라진 unsaved/unpinned/unliked 이력은 추정하지 않는다.
- source별 pair 수, 사용자 coverage, missing timestamp, cap 도달 수, playlist 투영 수와 제외 수를 artifact diagnostics에 저장한다.
- 직접 행동은 행동별 연속 half-life를 사용한다. saved/pinned 60일, watched 180일, passed 90일이며 favorite은 감쇠하지 않는다. 오래된 timestamp 행동은 0.05까지 낮아질 수 있고 영구 40% floor는 사용하지 않는다.

### 4.5 점수 해석

`model_score`는 여러 사용자와 feature의 학습 결과다. 다음과 같이 해석하지 않는다.

```text
금지: 이 배우 때문에 LightFM이 추천했다.
허용: LightFM model score가 후보를 생성했고, 별도 ontology 분석에서 이 배우 연결이 확인됐다.
```

## 5. 전체 추천 흐름

```text
1. LightFM 장기 후보 생성
2. 장기 profile 기반 ontology 후보 독립 생성
3. 최근 행동 기반 short-term ontology 후보 생성
4. 신규/희소 영화 ontology 후보 보충
5. source별 점수 정규화와 순위 후보 최대 150개 병합
6. metadata·watched/passed·상태·OTT hard filter를 먼저 적용
7. 통과 순서대로 활성 후보 최대 100개 확정
8. 활성 후보에 대한 ontology evidence 계산
9. 상세 분석 이후에도 같은 hard filter를 방어적으로 재확인
10. 개인 정책 가감점 적용
11. 반복 감점과 결정적 MMR 재정렬
12. 최종 영화, 점수 구성, 추천 이유 저장
```

단기 취향은 LightFM 후보에 가점만 주는 방식으로 제한하지 않는다. 범죄 취향 사용자가 최근 로맨스 행동을 보이면 로맨스 관련 후보가 별도 source에서 들어올 수 있어야 한다.

## 6. 후보 source

V3 1차 source:

```text
model
  LightFM 장기 취향 후보

long_term_ontology
  장기 positive concept 기반 ontology 후보

short_term_context
  최근 positive concept 기반 ontology 후보

ontology_cold_item
  행동이 적은 영화의 feature/evidence 기반 후보

cold_start
  학습 가능한 사용자 표현이 부족할 때의 rule 후보
```

LightFM과 다른 source를 병합한 순위 후보는 최대 150개다. 상위 100개가 기본 후보이고 다음 순위 50개는 hard filter 탈락을 채우는 예비 후보다. metadata와 OTT 자격 확인 후 ontology 상세 분석과 최종 정책에 전달하는 활성 후보는 계속 최대 100개다.

V3 1차에서 제외:

- random quota
- 신작 강제 quota
- long-tail 강제 quota
- 낮은 노출량 기반 exploration

exploration은 정확도 기준선이 안정된 뒤 별도 source로 추가한다.

## 7. Hard filter

점수 계산으로 복구할 수 없는 탈락 조건이다.

- `adult = true`
- 사용자가 watched한 동일 영화
- 사용자가 passed한 동일 영화
- 같은 페이지/session에서 이미 반환한 영화
- 서비스 필수 데이터가 없는 영화
- 서비스에서 명시적으로 차단한 상태
- `subscribed_only`에서 사용자의 구독 OTT로 streaming할 수 없는 영화

`subscribed_only` 후보가 부족해도 전체 catalog로 fallback하지 않는다.

최소 vote count는 희소·신규 영화를 영구 제거할 수 있으므로 기본 hard filter로 고정하지 않는다.

## 8. 점수 계층

점수는 다음 component로 분리한다.

```text
model_raw_score
normalized_long_term_score
long_term_ontology_raw_score
normalized_long_term_ontology_score
normalized_short_term_score
candidate_selection_score
ontology_type_scores
normalized_ontology_score
policy_adjustments
final_score
```

LightFM raw score와 ontology raw score를 직접 더하지 않는다.

후보 선택 단계:

```text
long_term_selection_score
  = model_weight * normalized_long_term_score
  + ontology_weight * normalized_long_term_ontology_score

candidate_selection_score
  = (1 - drift_weight) * long_term_selection_score
  + drift_weight * normalized_short_term_score
```

model/ontology 상위 50개 일치율에 따라 model weight는 0.45~0.65, 장기 ontology weight는 0.55~0.35를 사용한다. 장기 ontology 후보는 상세 분석 전 100개 중 최소 20%가 생존하도록 보호한다. 한 source에 없는 후보의 해당 source 점수는 `0`이다. 강한 단기 변화에서는 short-term 후보가 장기 후보에 모두 밀리지 않도록 contextual source floor를 적용한다.

최종 단계:

```text
base_score
  = personal_component
  + ontology_component

final_score
  = base_score
  + recency_adjustment
  + ott_adjustment
  + quality_adjustment
  - negative_preference_penalty
  - repetition_penalty
```

각 정책 adjustment는 총 영향 상한을 가진다.

## 9. 점수 정규화

- source별 raw 후보 집합에서 통계를 계산한다.
- hard filter 후 활성 100개만 이용해 source 정규화를 다시 fit하지 않는다.
- robust z-score + sigmoid 또는 percentile을 기본 후보로 둔다.
- 후보가 1개이거나 모든 점수가 같으면 중립값 `0.5`를 사용한다.
- 동점은 movie ID로 결정적으로 정렬한다.
- 모델 build나 source가 다른 raw score를 같은 scale로 가정하지 않는다.

## 10. Passed와 부정 취향

동일 passed 영화:

- WARP positive에 포함하지 않음
- serving hard exclude

의미 관계 penalty:

- 장르 하나가 같다는 이유만으로 강하게 제거하지 않음
- keyword, director, actor, theme, mood처럼 구체적인 반복 부정 관계를 확인
- passed 행동 수와 관계의 일관성이 낮으면 penalty 제한
- 최근 passed를 오래된 passed보다 강하게 반영
- negative penalty 총량에 상한 적용

V2의 bounded semantic negative 구조는 참고하지만 V2 절대 가중치는 계승하지 않는다.

## 11. 최근성과 단기 취향

장기 학습 최근성:

- 오래된 positive의 sample weight를 낮춘다.
- timestamp가 없는 onboarding favorite는 decay하지 않는다.

단기 profile:

```text
최근 명시 행동 최대 50개
positive: saved, pinned, watched
negative: passed
concept: genre, keyword, actor, director, theme, mood
```

현재 구현 초기값:

```text
window                 30일
maximum actions        50
decay half-life        14일
evidence per feature   8
```

profile action strength는 LightFM sample weight와 분리한다.

| 행동 | profile strength | 방향 |
| --- | ---: | --- |
| favorite | `0.5` | positive, long-term only |
| watched | `0.75` | positive + 동일 영화 제외 |
| saved | `1.0` | positive |
| pinned | `1.0` | positive |
| passed | `1.0` | negative + 동일 영화 제외 |

영화 하나에 actor/keyword가 많다는 이유로 해당 영화가 profile 총량을 지배하지 않도록 관계군별 각 edge 기여에 `1/sqrt(영화의 해당 관계 수)`를 적용한다. 그 뒤 feature별 score cap과 top-K를 적용하며 원점수와 잘린 값, drop 수를 함께 기록한다. 이 값은 실제 사용자 분포를 보기 전 provisional이다.

단기 변화 강도는 다음을 관측한다.

- 최근 행동에서 새 concept이 차지하는 비율
- 장기 profile과 최근 profile의 차이
- 같은 방향의 행동이 연속되는 정도
- positive와 passed의 일관성

현재 drift confidence는 다음 세 component를 분리 기록한다.

```text
activity             min(1, recent_positive_action_count / 5)
family novelty       관계군별 최근 feature 중 장기에 없던 비율의 평균
positive consistency recent_positive / (recent_positive + recent_negative)
```

actor/keyword cardinality가 novelty를 지배하지 않도록 관계군별 값을 동일 비중으로 평균한다. 비교 가능한 장기 관계군이 없으면 novelty를 `0`으로 두고, passed만 있으면 drift confidence는 `0`이다.

단기 신호가 약하면 장기 후보 중심을 유지하고, 강하면 `short_term_context` 후보 비중을 높인다.

현재 S7 provisional 결합값:

```text
drift_weight                  drift_confidence * 0.45
contextual floor 시작         drift_confidence >= 0.60
contextual floor 최대         최종 후보의 25%
source normalization          tie-average percentile
동점                          model source 우선, source rank, movie ID
```

contextual floor는 short-term 후보를 25%로 제한하는 quota가 아니라 강한 drift에서 최소 coverage를 보장하는 장치다. 혼합 점수로 더 많은 short-term 후보가 선택되는 것은 허용한다.

### 11.1 단기 후보 갱신 정책

행동 저장, 즉시 정책 반영, 독립 단기 후보 생성은 서로 다른 단계다.

| 행동 | 즉시 반영 | 독립 단기 후보 갱신 |
| --- | --- | --- |
| passed 추가·해제 | blacklist, 제외, negative profile | 갱신하지 않음 |
| watched 추가 | 해당 영화 제외, positive profile | `0.75`로 누적 |
| pinned/saved 추가 | positive profile과 기존 후보 재정렬 | `1.0`으로 누적 |
| watched/pinned/saved 삭제 | 최신 profile에서 제거 | debounce 후 강제 갱신 |
| OTT 변경 | 최신 serving filter | 갱신하지 않음 |
| 온보딩 장르·영화 변경 | feature-only/cold-start 경로 | 별도 cold-start 갱신 |

단기 후보 생성용 누적 범위는 최근 `24시간`이며 같은 영화의 여러 positive는 가장 높은 가중치 하나만 사용한다. 다음 중 하나를 만족할 때만 독립 `short_term_context` 후보를 다시 생성한다.

```text
서로 다른 positive 영화 >= 3
또는
서로 다른 positive 영화 >= 2 이고 가중치 합 >= 2.0
```

기준 성립 후 마지막 positive 변경으로부터 `30초` 동안 추가 행동을 수집한다. 계속 행동하더라도 최초 성립 후 `2분`을 넘기지 않고 worker에 전달한다. positive 삭제는 잘못된 materialized evidence를 제거해야 하므로 같은 `30초` debounce를 거친 강제 갱신이다. 기준 미달 positive 한 건도 최신 DB profile을 통해 기존 100개 후보의 ontology/policy 재정렬에는 즉시 사용되지만, 비싼 reverse lookup은 실행하지 않는다.

단기 후보 cache signature는 ontology build, 사용자, cache format으로 검증한다. 저장 시점부터 `6시간 + 사용자별 결정적 0~30분 jitter` 뒤 만료하며 벽시계 bucket은 사용하지 않는다. 따라서 일괄 warm한 사용자도 같은 시각에 동시에 만료되지 않는다. profile version과 현재 시각은 signature에 넣지 않는다. 정상 cache가 있는 동안에는 기준을 충족한 누적 positive 갱신, positive 삭제, ontology build·cache format 변경만 후보를 다시 계산한다. watched/passed 영화는 cache hit 시 최신 제외 집합으로 제거하고 장기 후보가 빈자리를 채운다. Redis 장애·cache miss·손상·TTL 만료 시 DB reverse lookup으로 fallback한다.

LightFM 학습 반영은 이 online 갱신과 분리한다. 행동은 다음 학습 snapshot에 포함하고, V3 학습 scheduler가 준비되기 전에는 수동 build/publish 절차를 사용한다. 사용자 행동 하나마다 LightFM을 재학습하지 않는다.

## 12. OTT 정책

`mode=all`:

- 구독 OTT에서 streaming 가능한 영화에 작은 bounded bonus
- OTT가 없다는 이유만으로 탈락시키지 않음

`mode=subscribed_only`:

- 최신 `movie_otts.is_streaming` 기준 hard filter
- 전체 catalog fallback 금지

OTT는 LightFM preference feature로 사용하지 않는다. 최종 filter와 bonus 여부는 항상 최신 `user_otts`, `movie_otts`를 읽는 rule-based 정책으로 판단한다.

## 13. 품질 정책

품질은 개인 적합도를 대체하지 않는다.

- `vote_average`는 `vote_count` 신뢰도와 함께 사용
- popularity 단독 고득점 방지
- 품질 bonus 총량 제한
- 신규 영화는 낮은 vote count만으로 전부 제거하지 않음
- 필요하면 Bayesian weighted rating 사용

`popularity=10`, `vote_count=1` 같은 영화는 인기도만으로 높은 품질 점수를 받지 않는다.

## 14. 반복 감점과 MMR

V3 1차에 포함한다.

반복 대상:

- genre
- actor
- director
- theme
- mood

원칙:

- 영화 ID 중복은 항상 제거
- 같은 feature가 연속으로 과도하게 반복되면 점진적으로 감점
- 관련성이 충분히 높은 후보를 무작위로 교체하지 않음
- 같은 입력과 bundle에서는 같은 순서를 반환
- MMR은 개인 적합도와 후보 간 중복도를 함께 고려

이는 random exploration과 다른 정책이다. 반복 감점은 정확도 중심 V3 1차에 유지한다.

## 15. Cold-start

### 신규 사용자

정상 프론트 온보딩은 OTT, 선호 장르, 선호 영화를 각각 한 개 이상 요구한다. 따라서 기본 경로는 `장르 + 선호 영화 + OTT`이며, 장르-only·정보 없음은 부분 저장이나 직접 API 호출에 대비한 복구 경로다. OTT는 아래와 같이 후보 취향 점수를 만들지 않는다.

입력 우선순위:

1. onboarding favorite
2. onboarding genre
3. 서비스 기본 품질 기준

후보 source:

- favorite와 의미가 유사한 영화
- 선호 genre/keyword/theme/mood 기반 영화
- ontology feature로 표현 가능한 희소 영화
- 개인 신호가 전혀 없을 때 제한된 품질 fallback

구독 OTT는 취향 후보를 만들지 않는다. 요청 mode에 따라 후보를 filter하거나 availability 정책을 적용하는 serving context다.

학습 가능한 identity interaction이 없더라도 기존 feature vocabulary로 표현되는 사용자는 LightFM feature-only 점수를 사용할 수 있다. rule 후보와 feature-only 후보 중 더 나은 경로를 선택하거나 결합한다.

현재 병합은 온톨로지 rule을 우선한다.

```text
선호 영화 있음: feature-only LightFM 0.30 / ontology rule 0.70
장르-only 복구: feature-only LightFM 0.15 / ontology rule 0.85
```

선호 영화가 있으면 해당 영화에서 파생된 genre/keyword/actor/director/theme/mood가 rule 후보의 주 근거이며, 명시적 선호 장르는 넓은 방향을 보완한다. 장르-only 경로는 적어도 하나의 선호 장르가 직접 일치해야 하며, 장르에서 확장한 theme/mood는 후보 영화의 `overview_signal` evidence가 있을 때만 추가 근거로 사용한다. overview 근거가 없다는 이유만으로 직접 장르 일치 영화를 제거하지 않는다.

온보딩 선호 영화는 취향 근거로만 사용하고 LightFM·온톨로지·품질 fallback 후보의 제외 목록에 넣는다. 사용자가 이미 선호 영화로 지정한 작품 자체를 다시 추천하지 않는다.

장르-only 복구 경로는 행동·선호 영화 근거가 없으므로 다음 보수적 품질 정책을 후보 100개 선정 전에 적용한다. 정상 온보딩과 행동 기반 추천에는 이 신뢰 후보 계층을 적용하지 않는다.

```text
vote_count = 0       제외
vote_count >= 20     신뢰 후보: 항상 먼저 선정
1 <= vote_count < 20 보충 후보: 신뢰 후보가 부족할 때만 뒤에 선정

장르 포함률 = 일치 선호 장르 수 / 사용자 선호 장르 수
장르 집중도 = 일치 선호 장르 수 / 영화 전체 장르 수
장르 관련도 = 장르 포함률 0.70 + 장르 집중도 0.30

의미 관련도 = ontology semantic percentile * 장르 관련도
규칙 선정 점수 = 의미 관련도 0.65 + 신뢰 품질 0.35
```

신뢰 품질은 13절과 같은 vote confidence·평점·popularity 계산을 사용한다. 의미 원점수, overview support, 장르 관련도, 신뢰 품질, 규칙 선정 점수는 서로 덮어쓰지 않고 분리 기록한다.

후보 생성에 사용한 온보딩 feature는 콜드스타트 상세 분석에도 전달한다. retrieval score, overview support, genre relevance, reliable quality, rule selection, feature-only model, 최종 ontology/policy 점수는 분리 기록한다. 비슷한 온보딩 사용자의 행동 후보는 실제 사용자와 영화별 지지 수가 충분해진 뒤 추가한다.

### 신규 영화

- model build의 item identity mapping에는 없음
- 기존 ontology feature vocabulary로 표현되면 feature-only 후보 가능
- 새 feature column이 필요하면 새 model build 필요
- 최신 품질·OTT hard policy는 별도 적용

### 모델 장애

- 손상된 V3 artifact를 V1 결과로 위장하지 않음
- 활성 bundle이 없으면 명시적인 fallback 상태 기록
- 새 bundle 실패 시 직전 정상 bundle 유지

## 16. 추천 이유와 진단

허용되는 이유:

```text
저장한 영화와 같은 생존 테마가 연결됨
최근 본 로맨스 영화와 mood가 유사함
선호 감독의 다른 영화임
구독 OTT에서 볼 수 있음
```

허용하지 않는 설명:

```text
LightFM이 배우 때문에 추천함
잠재 벡터의 특정 차원이 비슷함
ontology score가 높으므로 반드시 좋아함
```

저장 단위:

```text
model score/source rank
ontology type score/evidence path
policy별 adjustment
final score/rank
사용자 노출 reason code
model/ontology/policy version
```

사람은 이 진단을 보고 tuning 가설을 세울 수 있지만 시스템이 추천 이유만으로 LightFM parameter를 자동 변경하지 않는다.

## 17. 불변 정책

- watched/passed 동일 영화 재노출 0
- 한 응답 내 영화 ID 중복 0
- `subscribed_only` OTT 위반 0
- source와 점수 구성 추적 가능
- 사용자별 실패가 다른 사용자 결과를 훼손하지 않음
- 새 후보 snapshot이 불완전하면 이전 정상 snapshot 유지
- ontology evidence와 LightFM attribution 분리
- user-movie pair는 학습 matrix에서 최대 한 행
- playlist 파생 한 이벤트의 전체 movie unit 합은 최대 1
- social signal이 직접 행동보다 강해지지 않음
