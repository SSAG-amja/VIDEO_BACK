# V3 추천 시스템 설계 및 구현 계획

## 0. 문서 목적

이 문서는 현재 V1/V2 추천 시스템을 대체할 V3의 목표 구조와 단계별 구현 계획을 정의한다.

V3의 핵심 구조는 다음 세 계층이다.

```text
LightFM
= 어떤 영화를 추천할지 사용자 행동으로 학습하는 계층

온톨로지
= 사용자 취향과 영화 사이의 의미 관계를 표현하고 분석하는 계층

정책 엔진
= 추천 결과를 서비스 목적에 맞게 조정하는 계층
```

V3는 V2의 규칙 점수 계산기를 계속 확장하는 시스템이 아니다. LightFM이 추천 후보와 기본 순위를 만들고, 온톨로지와 정책 엔진은 각자 분리된 책임으로 후보를 분석하고 재정렬한다.

이 문서에서 숫자로 제시하는 가중치와 기준값은 최초 실험값이다. 고정 정책으로 간주하지 않고 동일한 평가 데이터로 검증한 뒤 확정한다.

---

## 1. 목표와 비목표

### 1.1 목표

- 실제 사용자 행동을 학습하는 협업 필터링을 추천의 중심에 둔다.
- 영화 ID뿐 아니라 온톨로지 feature를 함께 학습하는 하이브리드 LightFM을 구축한다.
- 온톨로지를 이용해 사용자와 후보 영화 사이의 의미 관계를 별도로 계산한다.
- 최근성, 부정 취향, OTT, 품질, 다양성 정책을 모델 밖에서 통제한다.
- 최종 점수의 구성과 정책별 변경 근거를 저장한다.
- 추천 이유는 실제 온톨로지 경로와 적용 정책을 근거로 생성한다.
- 신규 사용자와 신규 영화의 cold-start 경로를 명시적으로 제공한다.
- V1/V2와 동일한 입력 및 API 응답 계약을 유지하면서 엔진을 전환할 수 있게 한다.
- 후보 생성 품질과 최종 재정렬 품질을 분리해서 평가한다.

### 1.2 비목표

- V3 1차 구현에서 GNN, KG embedding, Transformer 기반 순위 모델을 도입하지 않는다.
- LightFM의 잠재 벡터를 사람이 이해할 수 있는 추천 이유로 포장하지 않는다.
- 정책 가중치를 운영 결과에 따라 자동으로 변경하지 않는다.
- 요청마다 LightFM을 다시 학습하지 않는다.
- OTT 제공 여부처럼 자주 바뀌는 값을 모델만으로 판단하지 않는다.
- V1/V2 코드를 제거하거나 기존 API·데이터 계약을 변경하지 않는다.
- V3 1차 구현에서는 랜덤·인기·신작·long-tail 후보를 별도 quota로 섞는 exploration을 구현하지 않는다.

### 1.3 현재 범위 결정: exploration 후순위

V3 1차 목표는 후보 회수율과 개인별 순위 정확도 개선이다.

```text
1차 구현
  LightFM 정확도
  ontology feature 기여도
  부정 취향/최근성/OTT/품질 정책
  장르/theme/mood/actor/director 반복 감점
  MMR 기반 결정적 재정렬
  cold-start

후속 구현
  랜덤 후보 혼합
  신작/long-tail/낮은 노출량 source
  exploration 강제 quota
```

반복 감점은 추천 목록이 같은 장르, theme, mood, actor, director로 과도하게 채워지는 것을 막는 결정적 정책이므로 1차 구현에 유지한다. 영화 ID 중복 제거와 watched/passed 재노출 방지도 그대로 유지한다.

V3 1차 후보 100개는 LightFM, ontology 신규 영화와 cold-start source로만 구성한다. exploration source가 없기 때문에 정확도 지표를 탐색 후보 혼입 없이 측정할 수 있다.

---

## 2. 최종 아키텍처

### 2.1 전체 데이터 흐름

```text
PostgreSQL
  ├─ 사용자 행동
  ├─ 온보딩 선호
  ├─ 영화 메타데이터
  └─ 온톨로지 그래프
       |
       v
[오프라인 학습 파이프라인]
  1. 학습 데이터 추출 및 시점 분할
  2. 사용자-영화 interaction 행렬 생성
  3. 온톨로지 sparse feature 행렬 생성
  4. LightFM 학습 및 오프라인 평가
  5. 모델 artifact 검증 및 활성화
       |
       v
[후보 생성 계층]
  LightFM 장기 취향 top-N
  + short-term context top-N
  + 신규 영화 ontology 후보
  + cold-start 후보
       |
       v
[온톨로지 분석 계층]
  사용자 취향과 후보 영화의
  장르/키워드/감독/배우/테마/분위기 의미 관계 계산
       |
       v
[정책 엔진]
  hard filter
  + 최근성/부정 취향/OTT/품질 조정
  + 반복 감점/MMR 재정렬
       |
       v
[최종 추천]
  순위 + 점수 구성 + 추천 이유 + 진단 기록
```

### 2.2 계층별 책임

| 계층 | 책임 | 책임지지 않는 것 |
| --- | --- | --- |
| LightFM | 협업 신호와 feature를 학습하고 후보 및 기본 점수 생성 | OTT hard filter, 설명 문구 |
| 온톨로지 | 사용자-영화 의미 일치도, 관련 경로, 부정 취향 관계 계산 | 최종 추천 여부 단독 결정 |
| 정책 엔진 | 서비스 정책 적용, 점수 조정, 재정렬, 정책 근거 기록 | 잠재 취향 학습 |
| Explainer | 온톨로지 증거와 정책 결과를 사용자용 이유로 변환 | 근거가 없는 모델 추론 설명 |
| Serving | 모델/후보 로드, 요청 처리, 저장, fallback | 모델 학습 |

### 2.3 핵심 경계

온톨로지는 두 계층에 데이터를 제공한다.

```text
온톨로지 -> LightFM
영화와 사용자의 sparse feature 제공

온톨로지 -> 정책 엔진
후보별 의미 일치도와 부정 관계 증거 제공
```

그러나 각 계층의 결과는 섞어서 저장하지 않는다.

```text
model_score          = LightFM의 학습 점수
ontology_score       = 의미 관계 분석 점수
policy_adjustment    = 정책별 가감점
recommendation_reason = 사용자에게 표시 가능한 ontology/policy 근거
final_score          = 재정렬에 사용한 최종 점수
```

`recommendation_reason`은 `model_score`의 인과적 attribution이 아니다. 두 값이 같은 actor, genre 등의 feature를 공유하더라도 "LightFM이 그 feature 때문에 추천했다"고 해석하지 않는다. 추천 근거는 사람이 이상 패턴을 발견하고 LightFM feature, 행동 weight, loss, regularization 등의 튜닝 가설을 세우는 관측 자료로만 사용한다. 실제 개선 여부는 재학습 ablation으로 확인한다.

---

## 3. 현재 저장소에서 재사용할 부분

### 3.1 그대로 유지할 계약

- `app/schemas/recsys.py`의 `RecommendationResponse`
- `RecommendationMode.ALL`
- `RecommendationMode.SUBSCRIBED_ONLY`
- 영화 ID를 순서대로 반환하는 API 형식
- `request_id`, `feed_session_key`, `refresh_count`
- Redis의 최근 노출 및 blacklist 개념
- `recommendation_runs` 실행 기록
- `ontology_build_id`를 통한 그래프 버전 추적
- 요청별 후보 및 최종 응답 snapshot 저장 방식

### 3.2 구현 기준 우선순위

V3 구현에서 V1과 V2의 참고 범위를 다음처럼 고정한다.

| 책임 | 기준 | 원칙 |
| --- | --- | --- |
| 행동 의미와 장기 profile | V1 기본, V2 선택 | V1을 기준선으로 하되 V1에 없는 신호나 검증상 우수한 V2 방식은 선택 채택 |
| 후보 보충, exclusion, OTT, cold-start, worker 안정성 | V1 기본, V2 선택 | V1 정책 목적과 처리 순서를 불변 기준으로 두고 정책 단위 비교 후 교체 가능 |
| 온톨로지 node/edge/build/evidence | V2 | 현재 그래프와 build 자산을 audit한 뒤 V3 schema로 재정의 |
| LightFM 학습·후보 생성 | V3 신규 | V1/V2 수동 점수식을 복사하지 않고 학습 계층으로 구현 |
| API·DB snapshot·노출 event | 기존 공통 계약 | 엔진 버전과 무관한 저장·응답 계약만 호환 유지 |

추천 정책의 기본 비교 기준은 V1이다. 다만 다음 중 하나를 만족하면 V2 방식을 선택할 수 있다.

- V1에 해당 기능이나 정책이 없음
- 동일 입력과 지표에서 V2 방식이 더 낫다고 확인됨
- V2 방식이 정확도 저하 없이 정책 위반, 실행 시간 또는 관측 가능성을 개선함

채택 시 `V1 유지 / V2 채택 / V3 신규` 중 결정을 정책 registry에 기록하고 비교 근거와 조정값을 남긴다. V2의 추천 결과나 가중치가 더 최근에 작성됐다는 이유만으로 계승하지 않으며, V2 전체 pipeline을 통째로 기준선으로 삼지도 않는다.

V1 기준 파일:

- `app/jobs/recsys/v1/worker.py`: 행동 집계, profile, 후보 source, 원자적 교체, advisory lock
- `app/services/recsys/v1/recommendation.py`: blacklist, OTT, pagination, 동적 보충
- `app/services/recsys/v1/dynamic_retriever.py`: cold-start와 contextual DB 조회 구조
- `app/services/recsys/v1/interaction_cache.py`: 최근 행동과 blacklist cache

### 3.3 V2에서 제한적으로 참고할 온톨로지 자산

| V2 자산 | V3에서 참고하는 범위 | 참고하지 않는 범위 |
| --- | --- | --- |
| `graph_builder.py` | node/edge build와 활성 build 구조 | V2 추천 점수와 feature 활성 기본값 |
| `overview_signal_extractor.py`, `materialize_overview_edges.py` | theme/mood 원천 추출과 provenance | active build 사후 변경 방식 |
| `validate_assets.py`, `assets/ontology` | controlled vocabulary와 asset 검증 | V2 relation 이름을 그대로 유지하는 것 |
| `candidate_generator.py` | set-based graph query와 MATERIALIZED 최적화 형태 | 품질 조건, 후보 eligibility, graph score를 최종 추천 점수로 쓰는 방식 |
| ontology ORM/migration | 기존 table 호환성과 병행 build | V2 schema를 V3 요구사항 없이 그대로 복사하는 것 |
| `recommendation_feed_events` | 공통 노출/성과 event 저장 계약 | V2 session 점수나 재정렬 정책 |

### 3.4 검증 없이 직접 재사용하지 않을 V2 추천 로직

- `profile_builder.py`의 행동별 절대 feature 점수
- `candidate_generator.py`의 품질·장르 수·후보 탈락 정책
- `ranker.py`, `scorer.py`의 graph match 합산 점수
- `dynamic_reranker.py`, `session_state.py`의 session 점수와 재정렬 방식
- `post_processor.py`의 exclusion 집합을 V1 검증 없이 그대로 복사하는 것
- 인기도와 평점을 개인화 점수의 대체물로 사용하는 방식
- 전체 영화 graph 조회를 요청마다 수행하는 방식

V2 코드를 import해 V3 추천 정책을 우회 구현하지 않는다. V2 정책을 채택할 때도 필요한 query나 계산을 V3 모듈로 분리하고, 해당 정책이 V1보다 나은 이유를 동일 입력과 지표로 확인한다.

### 3.5 V1 기본 기준과 V2 선택 채택

V3의 정책 엔진은 V1의 정책 구조를 기본 기준선으로 삼는다. 단, V1의 절대 점수와 고정 비율은 그대로 복사하지 않으며, V1에 없는 기능 또는 V2가 더 낫다고 검증된 정책은 V2에서 선택적으로 가져온다.

#### 그대로 계승할 원칙

- watched와 passed 동일 영화 재노출 제외
- Redis blacklist를 통한 즉시 반영과 DB 장기 상태의 병행
- 행동 timestamp에 따른 최근성 감쇠
- saved, pinned, watched와 playlist/community 장기 신호 구분
- 플레이리스트 경유 행동을 영화 수로 나누어 과도한 증폭 방지
- 반복 행동에 선형 합산 대신 로그 감쇠 적용
- 이미 선정한 후보와 보조 후보의 중복 제거
- 후보 부족 시 동적 후보로 보충
- `mode=all`에서 구독 OTT 보너스
- `mode=subscribed_only`에서 OTT hard filter 및 전체 영화 fallback 금지
- 성인 영화 제외
- source와 source별 기여도 기록
- 사용자별 후보 pool 원자적 교체
- 최소 후보 수 미달 또는 검증 실패 시 기존 정상 결과 유지
- transient DB 오류만 제한적으로 재시도
- worker advisory lock과 실행 요약
- 행동이 없는 신규 사용자의 cold-start 결과를 일반 worker가 무조건 덮어쓰지 않는 원칙

#### 구조만 참고하고 V3에서 다시 조정할 정책

| V1 정책 | V1 값/방식 | V3 처리 |
| --- | --- | --- |
| 행동 점수 | passed `-3`, pinned `+4`, watched/saved `+6` 등 | LightFM sample weight와 정책 penalty로 분리 후 평가로 재결정 |
| feature 점수 | genre `8`, keyword `5`, director `6`, actor `3` | LightFM feature와 ontology evidence로 분리하고 정규화 |
| 후보 비율 | content `60%`, collaborative `20%`, explore `20%` | content와 collaborative를 hybrid LightFM이 통합하므로 직접 계승하지 않음 |
| cold-start 비율 | favorite similar `50%`, genre popular `30%`, fallback `20%` | V3 cold-start 기준선으로 사용한 뒤 ontology theme/mood 포함 실험 |
| 영화 품질 점수 | `popularity*0.05 + vote_average` | vote count 신뢰도와 정규화를 포함한 별도 quality policy |
| OTT 보너스 | 전체 점수 `1.15`배 | 정규화 점수에 bounded adjustment로 변경 |
| source 병합 | 서로 다른 원시 점수를 단순 합산 | source별 정규화 후 결합하고 component를 별도 저장 |
| 탐색 | 인기 영화 중심 고정 quota | V3 1차에서 제외하고 정확도 기준선 확정 후 실험 |
| session shuffle | seed로 전체 순서 shuffle | V3 1차에서 제거하고 결정적 순위 유지 |
| 협업 필터링 | 사용자-사용자 cosine similarity | LightFM latent collaborative signal로 교체 |

#### V1 정책 불변 기준

V3가 V1보다 학습 구조가 복잡해져도 다음 정책 품질은 낮아지면 안 된다.

- excluded 영화 재노출 0
- 중복 0
- `subscribed_only` 위반 0
- 후보 보충 후 가능한 범위에서 페이지 수량 충족
- worker 일부 사용자 실패가 다른 사용자의 결과를 훼손하지 않음
- 추천 결과에서 source와 점수 구성 추적 가능

#### 정책별 출처 결정 기록

정책 단위로 다음 registry를 유지한다.

```text
policy_id
v1_behavior
v2_behavior
selected_source  # v1 | v2 | v3_new
selection_reason
comparison_dataset
quality_metrics
latency_metrics
hard_policy_violations
config_version
```

V1에 없는 V2 기능은 `selected_source=v2`로 바로 고정하지 않고 V3 요구사항과 부작용을 먼저 검사한다. V1과 V2 모두에 있는 정책은 동일한 후보 입력에서 policy on/off 또는 구현 A/B를 비교하고 채택한다. 현재 V2에서 우선 검토할 후보는 ontology evidence 진단, bounded semantic negative, 노출 event 관측과 set-based graph query이며, V2의 전체 추천 점수식은 채택 대상이 아니다.

### 3.6 V2 온톨로지에서 정의만 되었거나 비활성화된 기능 처리

V3에서는 config, schema 또는 점수식에 이름만 있고 실제 데이터 흐름에서는 사용되지 않는 feature를 허용하지 않는다.

현재 V2에서 특별히 확인된 항목:

| 항목 | V2 상태 | V3 원칙 |
| --- | --- | --- |
| actor node | 코드 기본값은 `False`, 활성 build 3은 option으로 `true` | V3 ontology build 필수 node |
| `has_actor` edge | 코드 기본값은 `False`, 활성 build 3은 option으로 `true` | V3 ontology build 필수 direct edge |
| overview theme/mood | build option은 `False`, 활성 build에 사후 materialize | offline 추출 검증 후 V3 build 내부에서 materialize |
| actor profile score | profile 필드는 채워질 수 있으나 actor graph가 없으면 candidate match 0 | LightFM item feature, runtime profile, ontology evidence에서 모두 사용 |
| theme/mood profile score | asset rule edge만 사용하고 overview 파생은 기본 비활성 | asset rule과 overview signal의 source를 구분해 모두 사용 |
| `available_on` | graph edge weight가 `0.0`이라 graph relevance에는 사실상 미반영 | OTT evidence와 정책 입력으로 반드시 사용하고 모델 feature는 ablation |
| profile feature limit | 정의된 top-K 이후 feature가 조용히 탈락 | feature별 retained/dropped 수와 점수 비율 기록 |

V3 production build에서는 actor와 overview semantic signal을 기본적으로 활성화한다. 데이터가 없거나 build 비용 문제로 비활성화해야 한다면 단순 `False` 기본값으로 숨기지 않고 다음 중 하나로 처리한다.

- build를 실패 처리
- 명시적인 degraded mode로 실행하고 run snapshot에 사유 기록
- 해당 feature를 제외한 별도 ablation 모델로 이름과 version을 분리

### 3.7 Feature 사용 완료 조건

feature를 “구현했다”고 판단하려면 다음 경로가 모두 연결되어야 한다.

```text
원천 DB/asset
-> ontology node/edge
-> feature registry
-> registry에 선언된 consumer
   - LightFM sparse item/user feature
   - runtime user preference profile
   - 후보별 ontology evidence
   - policy 또는 explanation
-> consumer별 사용량 진단
```

모든 feature가 모든 계층에서 점수로 사용될 필요는 없다. 예를 들어 OTT는 모델 feature로 선택 적용할 수 있지만, 정책 계층에서는 반드시 사용해야 한다. 각 feature는 registry에서 `required`, `optional`, `disabled` 상태와 consumer를 명시하고, `required` consumer의 downstream 값이 비어 있을 때 build 검증을 실패 처리한다.

필수 feature registry 예시:

| feature | LightFM item | LightFM user | runtime profile | ontology evidence | policy | explanation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| genre | 필수 | 필수 | 필수 | 필수 | 다양성 | 필수 |
| keyword | 필수 | 선택 | 필수 | 필수 | 부정 취향 | 필수 |
| director | 필수 | 선택 | 필수 | 필수 | 반복 방지 | 필수 |
| actor | 필수 | 선택 | 필수 | 필수 | 반복 방지 | 필수 |
| theme | 필수 | 선택 | 필수 | 필수 | 부정 취향/다양성 | 필수 |
| mood | 필수 | 선택 | 필수 | 필수 | 단기 취향/다양성 | 필수 |
| OTT | 선택 | 선택 | 필수 | 필수 | 필수 | 필수 |

`선택`은 구현하지 않아도 된다는 뜻이 아니라, ablation 결과에 따라 모델 입력 여부를 결정한다는 뜻이다. 원천부터 진단까지의 데이터 경로 자체는 구현한다.

온톨로지 node/relation/evidence/build 최적화의 상세안은 `z_v3_docs/v3_ontology_redesign.md`를 기준으로 한다.

---

## 4. 데이터 설계

### 4.1 현재 데이터의 중요한 한계

현재 `user_interactions`는 `(user_id, movie_id)`별 최신 상태를 저장한다.

```text
is_pinned + pinned_at
is_watched + watched_at
is_passed + passed_at
```

따라서 상태를 해제하면 과거 행동 이력이 사라진다. 이 데이터만으로도 V3 MVP 학습은 가능하지만 다음에는 부족하다.

- 행동 발생 순서 복원
- 과거 여러 번의 행동 집계
- 정확한 시간 기준 train/test 분할
- 노출되었지만 선택하지 않은 항목 분석
- 정책별 추천 이후 전환율 분석

`playlist_movies.created_at`은 saved 시점을 제공하지만 삭제된 saved 이력은 남지 않는다. `user_favorite_movies`도 시간 정보가 없다.

### 4.2 단계별 데이터 전략

#### MVP

현재 상태 테이블을 snapshot으로 읽어 학습한다.

- saved: `playlist_movies`
- pinned/watched/passed: `user_interactions`
- onboarding favorite: `user_favorite_movies`
- onboarding genre: `user_genres`
- OTT 구독: `user_otts`
- 노출 및 dwell: `recommendation_feed_events`

#### 운영형 V3

불변 행동 이벤트를 추가로 저장한다.

권장 event 예시:

```text
saved
unsaved
pinned
unpinned
watched
unwatched
passed
unpassed
exposed
short_dwell
long_dwell
```

새 테이블을 만든다면 원본 상태 테이블을 대체하지 않고 추천 학습용 event log로 둔다. 기존 `recommendation_feed_events`를 확장할 수도 있지만, 노출 이벤트와 사용자 명시 행동의 소유권 및 저장 시점을 먼저 확인해야 한다.

### 4.3 학습 대상 사용자와 영화

사용자:

- 삭제되지 않은 사용자
- 최소 한 개 이상의 학습 가능한 positive 행동이 있는 사용자
- 행동이 없는 사용자는 모델 학습 대상이 아니라 cold-start 대상으로 유지

영화:

- DB에 존재하고 서비스 노출이 가능한 영화
- `adult = false`
- 필수 메타데이터가 있는 영화
- 시청 가능 여부와 품질 조건은 학습 데이터 삭제보다 serving 정책에서 처리

영화를 학습 행렬에서 지나치게 제거하면 신규/희소 영화가 LightFM feature를 통해 일반화할 기회를 잃는다. 학습 catalog 조건과 최종 노출 조건은 분리한다.

---

## 5. LightFM 학습 설계

### 5.1 모델 형태

V3의 기본 모델은 identity feature와 ontology feature를 모두 사용하는 hybrid LightFM이다.

```text
user representation
= user identity embedding
 + onboarding/user feature embeddings

movie representation
= movie identity embedding
 + ontology item feature embeddings
```

identity feature를 제거하지 않는다.

- identity feature가 있어야 기존 사용자-영화 협업 패턴을 직접 학습할 수 있다.
- ontology feature가 있어야 상호작용이 적거나 없는 영화로 일반화할 수 있다.

### 5.2 Positive interaction

MVP의 기본 loss 후보는 `WARP`다. 상위 순위 최적화가 목적이므로 positive interaction만 학습 입력으로 사용한다.

positive 후보:

- saved
- pinned
- watched
- onboarding favorite
- 이후 단계에서 검증된 community 강한 행동

행동별 상대 강도는 config에 둔다. 최초 방향은 다음과 같다.

```text
saved / pinned = 강한 긍정
watched        = 긍정이지만 serving에서는 소비 완료로 제외
favorite       = 온보딩 prior
```

같은 사용자-영화에 여러 positive 행동이 겹치면 무제한 합산하지 않는다.

```text
interaction_weight
= 대표 행동 weight
 + 중복 행동 confidence bonus

최종 weight는 상한 적용
```

구체적인 수치는 V1/V2와 V3를 같은 평가셋에서 비교해 정한다.

### 5.3 Passed 처리

`WARP` MVP에서 passed를 음수 interaction 값으로 직접 넣지 않는다.

MVP 처리:

- 학습 positive에서 제외
- serving에서 동일 영화 hard exclude
- 온톨로지의 장르/키워드/인물/테마 관계로 부정 취향 penalty 계산
- 평가 시 passed 재노출 수를 별도 측정

추후 실험:

- `logistic` loss에서 명시적 negative label 사용
- WARP 모델과 logistic 모델의 offline 비교
- 실제 노출 이력이 충분해진 뒤 hard negative 실험

loss별 의미가 다르므로 하나의 interaction 행렬을 모든 loss에 그대로 사용하지 않는다.

### 5.4 행동 최근성

행동 최근성은 LightFM sample weight와 정책 엔진에서 서로 다른 목적으로 사용할 수 있다.

- 학습: 오래된 positive의 학습 영향 감소
- 정책: 현재 사용자 취향과 가까운 후보에 단기 가점

동일한 최근성 값을 두 번 과도하게 반영하지 않도록 각 계층의 최대 영향도를 제한한다.

최초 실험 bucket:

```text
0~30일      1.0
31~90일     0.8
91~180일    0.6
181일 이상  0.4
timestamp 없음 1.0
```

### 5.5 온톨로지 item feature

LightFM에는 그래프 자체가 아니라 그래프에서 추출한 sparse feature를 전달한다.

```text
genre:{genre_id}
keyword:{keyword_id}
director:{people_id}
actor:{people_id}
theme:{theme_key}
mood:{mood_key}
ott_streaming:{ott_id}
```

관계의 `weight * confidence`는 feature weight로 전달할 수 있다.

예:

```text
movie 101
  genre:28                    1.00
  keyword:818                 0.70
  director:525                0.90
  theme:survival              0.82
  mood:tense                  0.65
```

feature cardinality 관리:

- 현재 `movie_actors`에는 cast order가 없으므로 근거 없이 주연을 추정하지 않음
- MVP는 매핑된 actor를 사용하되 전역 빈도, 희소도와 사용자별 top-K로 크기를 통제
- 향후 cast order가 추가되면 주요 cast 우선 정책을 별도 적용
- 지나치게 희소한 keyword/person feature는 최소 빈도 기준 검토
- 장르, theme, mood는 우선 유지
- feature namespace를 반드시 분리
- ontology build가 달라지면 feature mapping도 새 artifact로 생성

OTT feature는 별도 ablation 대상으로 둔다. 영화의 `ott_streaming:{ott_id}`와 사용자의 `subscribed_ott:{ott_id}`를 함께 학습 feature로 제공할 수 있지만, 학습 당시의 OTT 정보를 최종 제공 가능 여부로 사용하면 안 된다.

LightFM은 multi-hop 의미를 자동 추론하지 않는다. `theme`, `mood` 또는 상위 개념 feature는 온톨로지 build 단계에서 명시적으로 materialize해야 한다.

overview 기반 theme/mood는 요청 시 텍스트를 다시 분석하지 않는다.

```text
overview signal offline 추출
-> extractor version과 confidence 저장
-> ontology `has_theme`/`has_mood` edge materialize
-> item feature matrix와 runtime profile에 사용
```

### 5.6 LightFM User feature

MVP:

- user identity
- onboarding genre
- onboarding favorite에서 추출한 genre/keyword/actor/director/theme/mood
- 선택 실험으로 subscribed OTT

OTT 구독 정보는 모델 feature로 사용할 수 있어도 정책 입력을 우선한다. OTT 제공 정보는 변경 주기가 빠르고 `subscribed_only`는 hard filter이기 때문이다.

행동 기반 동적 취향은 interaction 행렬이 담당한다. 같은 신호를 user feature에 중복해서 넣을 때는 실험으로 이득을 확인한 뒤 반영한다.

신규 사용자가 모델 학습 이후 가입하면 user identity mapping이 없으므로 다음 재학습 전까지 runtime cold-start profile을 사용한다.

### 5.7 Runtime 사용자 profile

LightFM user feature와 요청 시점의 사용자 취향 profile은 별도 객체로 둔다.

#### Long-term ontology preference profile

필수 구성:

```text
user_id
profile_type
action_counts
positive_movie_ids_by_action
negative_movie_ids
excluded_movie_ids
subscribed_ott_ids

positive feature scores
  genre_scores
  keyword_scores
  actor_scores
  director_scores
  theme_scores
  mood_scores

negative feature scores
  negative_genre_scores
  negative_keyword_scores
  negative_actor_scores
  negative_director_scores
  negative_theme_scores
  negative_mood_scores
```

profile source:

- onboarding genre
- onboarding favorite
- saved
- pinned
- watched
- passed
- V1에서 검증된 playlist/community 장기 행동

각 feature score에는 최소한 다음 provenance를 추적한다.

```text
어떤 행동에서 왔는가
어떤 영화에서 전파됐는가
행동 시각과 recent decay는 얼마인가
direct edge인지 overview/asset 파생 edge인지
최종 profile score에 얼마나 기여했는가
```

profile 계산 원칙:

- saved, pinned, watched가 같은 영화에서 겹칠 때 무제한 중복 합산하지 않음
- 최근 행동의 영향은 높이고 오래된 행동은 감쇠
- actor/director 한 명이 많은 영화에 등장한다는 이유로 profile을 독점하지 않도록 cap 적용
- theme/mood는 asset rule과 overview signal의 confidence를 반영
- passed 동일 영화는 exclusion으로 유지하고 feature 단위 negative는 일관성과 반복 횟수를 확인
- 각 feature type의 top-K 절단 전후 점수 합과 개수를 기록
- 전파 가능한 매핑을 가진 profile source가 존재하는데 해당 feature 결과가 비어 있으면 profile 생성을 실패 처리

#### V1 기반 Short-term preference profile

단기 취향 대응은 V1의 다음 흐름을 기준으로 확장한다.

```text
user:{user_id}:recent_actions
  최근 명시 행동 50개

user:{user_id}:movie:blacklist
  passed/watched 즉시 제외

precomputed recommendations
  기본 후보

dynamic retriever
  후보 부족 또는 최신 문맥 보충
```

V3는 별도의 V2 session DTO나 dwell/skip profile을 기준으로 삼지 않는다. 사용자 단위 최근 명시 행동을 읽어 단기 취향을 계산한다.

Redis profile:

```text
recent_actions
  action
  movie_id
  occurred_at

short_term_positive_concept_scores
short_term_negative_concept_scores
short_term_source_movie_ids
```

최근 행동은 V1처럼 최대 50개를 유지한다. 기존 `action:movie_id` 문자열 대신 timestamp를 포함한 versioned JSON event로 저장한다.

#### 단기 행동 해석

| V1 기반 행동 | 단기 의미 | 처리 |
| --- | --- | --- |
| saved/playlist_add | 강한 positive | 최근 concept profile에 반영 |
| pinned | positive | 최근 concept profile에 반영 |
| watched | 강한 positive + 동일 영화 제외 | concept 반영 후 blacklist |
| passed | negative + 동일 영화 제외 | negative concept 반영 후 blacklist |

V1의 행동 방향성과 blacklist 정책은 유지하되, V1의 절대 점수 `-3/+4/+6`을 그대로 더하지 않는다. V3 정규화 점수 범위에 맞는 상대 strength로 다시 config화하고 ablation한다.

초기에는 V1에 없는 dwell, skip, exposure를 단기 취향 점수로 사용하지 않는다. 필요하면 명시 행동 기반 단기 추천이 검증된 뒤 별도 단계에서 추가한다.

#### Concept 전파와 감쇠

최근 행동 영화에서 다음 ontology feature를 추출한다.

```text
genre
keyword
actor
director
theme
mood
```

```text
event_contribution
  = action_strength
  * edge_effective_strength
  * confidence
  * short_term_recency_decay
```

V1의 장기 최근성 bucket 정책과 원칙은 유지하지만 단기 변동에는 더 촘촘한 시간 단위를 사용한다. 최초안은 `0~1시간 / 1~6시간 / 6~24시간 / 이후 제외` bucket이며 운영 데이터 관측으로 조정한다.

- feature type별 score cap 적용
- 같은 영화의 상태 변경은 최신 상태 우선
- actor/keyword top-K 적용
- positive와 negative가 충돌하면 최신 명시 행동 우선
- 행동 영화와 graph path provenance 기록

#### 단기 취향 적용 순서

```text
1. LightFM 장기 취향 후보를 독립 생성 또는 materialized 결과에서 로드
2. Redis 최근 행동 50개로 short-term profile과 drift confidence 계산
3. 유효한 positive 단기 signal이 있으면 ontology 기반 short-term 후보를 독립 생성
4. source별 점수를 정규화하고 drift weight로 장기/단기 후보를 병합
5. blacklist와 hard eligibility를 적용하고 후보 pool을 최대 100개로 제한
6. 상세 ontology 분석, OTT/품질/부정 취향/반복 감점/MMR 적용
```

기준 후보를 재정렬하는 것만으로는 단기 취향과 관련된 영화가 LightFM 후보에 없을 때 대응할 수 없다. 따라서 short-term retrieval은 기존 후보의 coverage 부족 여부와 관계없이 유효한 positive signal이 있으면 실행한다. 단기 취향은 LightFM artifact나 장기 profile을 수정하지 않고 요청 시 후보 생성과 점수 혼합에만 사용한다.

#### V1 Dynamic Retriever 기반 독립 단기 Retrieval

V1의 dynamic retriever가 DB에서 동적으로 후보를 찾는 구조를 계승하되, V3에서는 후보 부족 fallback이 아니라 단기 취향을 위한 정식 candidate source로 확장한다.

trigger:

- 최근 24시간 안에 saved/pinned/watched가 1개 이상 존재
- 행동 해제나 최신 DB 상태를 반영한 뒤에도 positive event가 유효
- passed만 존재하는 경우 positive 후보를 생성하지 않고 exclusion과 negative 정책에만 사용

제한:

- contextual raw 후보 최대 100개
- 기존 후보와 중복 제거
- passed/watched blacklist 적용
- `subscribed_only` OTT 정책 유지
- 최소 ontology relevance 통과
- 랜덤 후보 사용 금지
- 최종 후보 pool은 계속 100개

drift confidence는 단순 이벤트 개수뿐 아니라 다음을 함께 사용한다.

```text
recent positive 행동 수
+ 서로 다른 영화에서 같은 concept가 반복된 정도
+ saved/watched/pinned의 상대 strength
+ concept 집중도
* 시간 감쇠
```

- 최근 positive 1개는 약한 signal로 처리
- 서로 다른 2개 이상의 영화에서 romance 같은 concept가 반복되면 강한 signal로 처리
- 강한 signal이고 eligible contextual 후보가 충분하면 최종 후보 100개 안에 초기값 20개를 우선 확보
- 이 확보량은 후보 recall을 위한 source floor이며 최종 20개에 대한 랜덤/다양성 quota가 아님
- 최종 노출 순위는 short-term relevance와 장기 적합도 및 정책 점수를 함께 사용

이 후보는 `short_term_context` source로 기록하며 보류한 exploration source와 구분한다. 예를 들어 장기 profile이 범죄 중심이어도 최근 서로 다른 로맨스 영화를 저장하거나 시청했다면, 기존 범죄 후보의 점수만 조정하지 않고 로맨스 concept에 연결된 영화를 새 후보로 조회한다.

short-term profile은 long-term profile이나 LightFM artifact를 수정하지 않는다. 원본 행동은 다음 정기 LightFM 학습에서 장기 취향에 반영한다.

#### V1 실시간 행동 경로 확장

- interaction 저장 성공 후 V1 recent action cache에 timestamp event 기록
- passed/watched는 기존 blacklist에 즉시 반영
- saved/unsaved와 playlist 변경도 recent action cache에 연결
- 행동 해제 시 stale event가 현재 상태를 잘못 반영하지 않도록 최신 DB 상태 확인
- Redis 장애 시 DB 원본 행동을 유지하고 단기 adjustment 없이 기본 추천으로 fallback
- 추천 조회 시 Redis recent action read 실패도 기본 후보와 DB blacklist로 fallback

#### Profile 사용 위치

| profile | LightFM 학습 | 후보 생성 | 온톨로지 분석 | 정책 엔진 |
| --- | ---: | ---: | ---: | ---: |
| LightFM user feature | 필수 | 필수 | 사용 안 함 | 사용 안 함 |
| long-term ontology profile | interaction/feature 원천 | cold-start 보조 | 필수 | 필수 |
| short-term profile | 사용 안 함 | 독립 contextual 후보 | 입력 | 필수 |

### 5.8 학습/검증 분할

무작위 row 분할을 기본값으로 사용하지 않는다.

가능한 경우:

```text
사용자별 과거 행동 -> train
사용자별 이후 행동 -> validation/test
```

현재 상태 snapshot만 있는 MVP에서는 완전한 시간 분할이 불가능할 수 있다. 이 경우:

- production snapshot 평가는 사용자별 positive holdout으로 별도 수행
- split seed와 대상 ID를 artifact에 저장
- held-out 영화가 train feature나 interaction에 positive로 유입되지 않았는지 검사

### 5.9 최초 비교 모델

한 번에 hybrid 모델만 만들지 않고 아래 순서로 비교한다.

1. Popularity baseline
2. LightFM identity-only
3. LightFM identity + 장르
4. LightFM identity + 전체 ontology feature

이 비교가 있어야 협업 필터링과 온톨로지 feature가 각각 실제로 기여했는지 확인할 수 있다.

---

## 6. 모델 artifact와 버전 관리

### 6.1 artifact 구성

권장 디렉터리:

```text
assets/ml_models/lightfm/
  v3-YYYYMMDD-HHMMSS/
    model.joblib
    dataset_mappings.json
    user_features.npz
    item_features.npz
    eligible_movie_ids.npy
    training_config.json
    metrics.json
    manifest.json

assets/ml_models/v3/
  current.json  # model_build_id + ontology_build_id + policy_version bundle pointer
```

대용량 모델 바이너리는 Git에 commit하지 않는다.

DB의 serving bundle row를 활성 상태의 원본으로 사용하고 `current.json`은 process reload를 위한 원자적 pointer로 사용한다. 둘의 bundle ID가 다르면 새 bundle을 로드하지 않고 직전 정상 bundle을 유지한다.

### 6.2 manifest 필수 값

```json
{
  "model_version": "v3-YYYYMMDD-HHMMSS",
  "engine_version": "v3.0.0",
  "trained_at": "ISO-8601",
  "data_cutoff_at": "ISO-8601",
  "ontology_build_id": 1,
  "config_hash": "...",
  "dataset_hash": "...",
  "random_seed": 42,
  "loss": "warp",
  "dimensions": {
    "users": 0,
    "movies": 0,
    "interactions": 0,
    "user_features": 0,
    "item_features": 0
  }
}
```

### 6.3 활성화 원칙

- 학습 중인 디렉터리를 serving에서 읽지 않는다.
- 학습, 평가, artifact 재로딩 검증이 모두 성공한 뒤 model/ontology/policy가 결합된 serving bundle의 `current.json`을 원자적으로 교체한다.
- API process는 모델을 요청마다 disk에서 읽지 않고 메모리에 cache한다.
- model version 변경 감지는 명시적 reload 또는 안전한 주기 polling으로 처리한다.
- 새 artifact 로딩 실패 시 직전 정상 모델을 유지한다.
- 활성 model이 없으면 기존 정상 V1 recommendations 또는 cold-start로 fallback한다.
- V3 ontology build가 새로 성공해도 호환되는 model build가 준비되기 전에는 V3 serving bundle만 단독 교체하지 않는다.

### 6.4 패키지 호환성 선행 검증

2026-08-20 dependency spike에서 다음 조합을 검증했다.

```text
Python 3.11.16
LightFM 1.17
NumPy 2.4.6
SciPy 1.17.1
scikit-learn 1.9.0
joblib 1.4.2
```

- 원본 LightFM 1.17은 Python 3.11용 wheel을 배포하지 않으므로 `python:3.11` builder에서 wheel을 생성한다.
- API runtime은 `python:3.11-slim`을 유지하고, OpenMP 확장 실행에 필요한 `libgomp1`만 설치한다.
- 검증 버전은 `requirements-recsys-v3.txt`로 공통 API 의존성과 분리한다.
- `app/jobs/recsys/v3/lightfm_dependency_spike.py`에서 fit, predict, feature-only predict, joblib save/load를 검증했다.
- interaction mapping에 없던 사용자와 영화도 학습 당시 존재한 feature column으로 표현하면 추론할 수 있다.
- 학습 당시 vocabulary에 없던 새 feature column은 기존 artifact로 추론할 수 없으므로 새 model build가 필요하다.
- artifact 재로딩 전후의 float32 prediction은 exact match했다.

이 결과는 dependency gate만 완료한 것이며 Phase 0 전체 완료를 의미하지 않는다.

---

## 7. 후보 생성 설계

### 7.1 후보 source

```text
model
= LightFM 상위 후보

ontology_cold_item
= 행동이 적거나 없는 영화 중 ontology 적합 후보

short_term_context
= 최근 명시 행동에서 추출한 ontology concept 기반 후보

cold_start
= 학습 가능한 사용자 표현이 부족한 경우의 규칙 후보

explore [후속]
= 정확도 기준선 확정 후 새로움과 long-tail 확보를 위해 실험할 후보
```

### 7.2 기본 후보 크기

기본 사용자별 후보 풀:

```text
CANDIDATE_POOL_SIZE = 100

LightFM 장기 후보 최대 100
+ short-term context 후보 최대 100
+ ontology 신규 영화 후보
+ cold-start 보조 후보
-> source score 정규화
-> 장기/단기 혼합 점수 계산
-> 중복 제거 및 기본 적합도 정렬
-> 최대 100개로 절단
-> 정책 엔진 재정렬
```

단기 signal이 없으면 `short_term_context` retrieval은 실행하지 않고 장기 기준선과 같은 결과를 낸다. signal이 있으면 LightFM 후보와 별도로 단기 ontology 후보를 생성한다. 두 retrieval의 raw 합집합은 일시적으로 100개를 넘을 수 있지만, 상세 온톨로지 분석과 정책 재정렬 전 최대 100개로 줄인다. 강한 drift가 감지되면 설정된 contextual source floor를 먼저 확보하고 나머지를 혼합 점수로 채운다. V3 1차에는 explore source를 병합하지 않는다.

최종 20개 지표가 낮을 때 먼저 `Recall@100`을 확인한다. 필요하면 모델 자체의 회수 한계를 진단하기 위해 `Recall@300/500`을 오프라인 보조 지표로만 계산하며, 이를 운영 후보 풀 크기로 사용하지 않는다.

### 7.3 후보 생성 방식

기존 사용자:

1. 해당 사용자의 LightFM 장기 후보를 생성
2. Redis 최근 positive 행동이 있으면 short-term ontology 후보를 별도 생성
3. 두 후보 source 안에서 각각 점수를 정규화
4. drift confidence에 따른 혼합 점수를 계산하고 watched/passed를 제외
5. 강한 drift에서는 contextual source floor를 적용
6. ontology 신규 영화와 필요한 cold-start 보조 후보 병합
7. 중복 제거 후 최대 100개 선택

신규 사용자:

1. model artifact mapping에 사용자가 있으면 identity + onboarding feature로 LightFM 예측
2. model build 이후 가입해 identity mapping에 없으면 feature-only user row 추론 지원 여부 확인
3. feature-only 추론이 Phase 0 spike에서 검증됐으면 LightFM과 cold-start rule 후보를 혼합
4. 지원되지 않거나 onboarding feature가 없으면 V1 기반 cold-start 정책만 사용

신규 영화:

1. model build 당시 catalog에 포함된 interaction 0개 영화는 ontology feature embedding으로 예측
2. model build 이후 추가되어 mapping에 없는 영화는 feature-only item 추론을 Phase 0 spike에서 검증
3. feature-only 추론이 불가능하면 다음 model build 전까지 `ontology_cold_item` rule 후보로만 사용
4. identity embedding만으로는 신규 영화 일반화가 안 되므로 ontology feature 존재 여부 검증

### 7.4 오프라인 materialization과 온라인 처리

권장 운영 구조:

```text
야간/주기 worker
-> 모델 학습
-> 사용자별 LightFM top-N 계산
-> recommendations 후보 pool 저장

API 요청
-> 저장된 후보 pool 조회
-> 최신 ontology/long-term/short-term evidence 계산
-> 정책 엔진 재정렬
-> 최종 목록 반환
```

신규 사용자와 운영 fallback을 위해 on-demand retrieval도 제공하되, 전체 사용자 요청마다 전체 영화 predict를 반복하는 것을 운영 기본 경로로 삼지 않는다.

장기 ontology profile도 요청마다 전체 행동과 graph를 다시 집계하지 않는다. model/ontology build에 묶인 materialized profile 또는 versioned cache를 사용하고, 온라인에서는 최근 행동으로 만든 short-term delta만 계산한다. cache miss 시에도 feature mapping과 후보 분석은 set-based query로 한 번에 조회한다.

### 7.5 대규모 Top-K와 온라인 Graph 조회 제한

현재 catalog가 약 117만 영화이므로 사용자별 전체 영화 점수를 dense matrix로 생성하면 메모리와 계산량이 급증한다.

1차 구현:

- `user_count * eligible_movie_count`와 예상 score 연산량을 학습 시작 전에 출력
- movie block 단위로 LightFM score를 계산하고 사용자별 top-100만 유지
- 전체 사용자×영화 score matrix를 메모리나 DB에 저장하지 않음
- user batch별 checkpoint와 실패 재개 지원
- 사용자별/전체 predict 시간, peak RSS, 처리량 기록

blockwise exact top-K가 materialization 주기를 만족하지 못하면 rollout 전에 MIPS/ANN 후보 검색을 별도 spike한다. LightFM item representation과 bias를 사용한 근사 검색의 `Recall@100`을 exact 결과와 비교하고, 허용 기준을 통과하기 전에는 활성화하지 않는다.

short-term ontology retrieval:

- 최근 profile concept를 `VALUES`, `unnest` 또는 임시 테이블로 한 번에 전달
- `(build_id, node_type, ref_id)`와 양방향 edge composite index 사용
- concept→movie 역방향 조회 후 type별 cap과 top-K를 SQL에서 먼저 적용
- 전체 graph scan과 후보별 N+1 query 금지
- `EXPLAIN (ANALYZE, BUFFERS)` 계획을 대표 입력과 실제 크기 표본에서 보관
- profile/retrieval/merge p50/p95와 반환 edge 수 기록

---

## 8. 온톨로지 의미 분석 계층

### 8.1 입력과 출력

입력:

- 사용자 positive/negative 행동
- onboarding 선호
- LightFM 및 보조 후보
- 현재 serving bundle에 고정된 `ontology_build_id`

출력:

```python
OntologyEvidence(
    movie_id=...,
    total_score=...,
    type_scores={
        "genre": ...,
        "keyword": ...,
        "director": ...,
        "actor": ...,
        "theme": ...,
        "mood": ...,
        "ott": ...,
    },
    positive_paths=[...],
    negative_paths=[...],
)
```

### 8.2 분석 원칙

- LightFM 점수를 다시 구현하지 않는다.
- 후보로 선택된 영화만 분석한다.
- 관계 유형별 점수를 분리한다.
- `available_on` 관계는 의미 증거로 제공하되 최신 DB의 OTT 제공 여부와 교차 검증한다.
- 직접 관계와 파생 관계를 구분한다.
- `edge.weight * edge.confidence`를 사용하되 후보별 상한을 둔다.
- 같은 의미가 여러 경로로 반복될 때 중복 증폭을 제한한다.
- 사용자 행동 최근성을 evidence 생성 시 반영한다.

### 8.3 추천 이유의 정확성

허용:

```text
"저장한 영화들과 생존 테마가 유사해요"
"선호한 SF 장르와 우주 탐사 키워드가 연결돼요"
"자주 저장한 감독의 영화예요"
```

금지:

```text
"LightFM이 이 영화를 좋아한다고 판단했어요"
"잠재 벡터의 7번 요소가 비슷해서 추천했어요"
```

추천 이유는 실제 `positive_paths`와 적용된 정책만 사용한다. 이것은 모델의 인과 설명이 아니라 추천 결과에 존재하는 의미적 근거라는 점을 코드와 문서에서 구분한다.

### 8.4 사람이 수행하는 LightFM 튜닝 루프

추천 근거를 이용한 LightFM 튜닝은 다음 절차를 따른다.

```text
후보별 분리 진단 수집
-> feature 유형별 이상 패턴 집계
-> 사람이 원인 가설 작성
-> LightFM feature/학습 config 변경
-> 새 model build 학습
-> 동일 split과 seed로 ablation
-> 지표와 정책 위반을 비교한 뒤 활성화 여부 결정
```

예:

| 관측 | 가능한 튜닝 가설 | 확인 방법 |
| --- | --- | --- |
| actor evidence가 있는 후보가 과도하게 반복 | actor feature 빈도 제한이나 weight가 약함 | actor feature on/off 및 frequency threshold ablation |
| saved 사용자의 관련 후보 model rank가 계속 낮음 | saved sample weight가 부족함 | 행동 weight만 바꾼 build 비교 |
| ontology 적합도는 높은데 candidate `Recall@100`이 낮음 | LightFM feature 구성 또는 학습 설정 문제 | identity-only/hybrid와 WARP parameter 비교 |
| model rank는 높은데 최종 순위가 낮음 | LightFM이 아니라 ontology/policy 결합 문제 | policy on/off와 score component 검사 |

추천 근거만 보고 LightFM parameter를 자동 변경하지 않는다. 원인 가설과 실제 변경의 효과는 model build 간 평가 결과로 분리해 기록한다.

---

## 9. 정책 엔진 설계

### 9.1 처리 순서

```text
1. hard eligibility filter
2. LightFM/short-term source score 정규화와 개인 적합도 결합
3. ontology score 결합
4. 개인 정책 가감점
5. 반복 감점과 MMR 재정렬
6. 페이지/session 동일 영화 중복 방지
7. 최종 점수와 근거 기록
```

### 9.2 Hard filter

점수 계산 전에 탈락시키는 조건:

- adult
- 사용자가 watched한 동일 영화
- 사용자가 passed한 동일 영화
- 최근 session에서 이미 노출한 영화
- 필수 영화 데이터 누락
- `subscribed_only`에서 사용자 OTT로 볼 수 없는 영화
- 서비스에서 명시적으로 차단한 상태

최소 vote count는 신규 영화까지 영구적으로 제거할 수 있으므로 hard/soft 적용을 별도 실험한다.

### 9.3 점수 정규화와 결합

LightFM raw score를 그대로 ontology 점수와 더하지 않는다. LightFM 점수는 사용자와 모델 버전에 따라 범위가 달라질 수 있다.

최초 방식:

- 후보 집합 내 LightFM score를 percentile 또는 robust z-score + sigmoid로 `[0, 1]` 정규화
- short-term 후보 점수는 LightFM과 별도로 해당 source 안에서 `[0, 1]` 정규화
- ontology score도 관계 유형별 cap 후 `[0, 1]` 정규화
- 정책 adjustment는 총 영향 상한 적용

source 후보가 1개이거나 점수가 모두 같으면 min-max 또는 z-score를 적용하지 않는다. 동점 source는 중립값 `0.5`를 사용하고 movie ID로 결정적 tie-break한다. 정규화 통계는 source별 raw 후보 집합에서 계산한 뒤 top-100 절단과 병합에 사용하며, 이미 절단된 최종 목록으로 다시 fit하지 않는다.

후보 100개를 고를 때의 retrieval 점수:

```text
candidate_selection_score
  = (1 - drift_weight) * normalized_long_term_score
  + drift_weight * normalized_short_term_score
```

한 source에만 존재하는 후보의 다른 source 점수는 `0`으로 둔다. 단, 강한 drift에서는 convex blend만으로 contextual 후보가 모두 밀리지 않도록 7장에서 정의한 contextual source floor를 먼저 적용한다. 후보 100개 선정 후에는 상세 ontology score와 정책을 포함해 최종 점수를 다시 계산한다.

초기 실험식:

```text
normalized_personal_score
  = normalize(candidate_selection_score)

base_score
  = 0.75 * normalized_personal_score
  + 0.25 * normalized_ontology_score

adjusted_score
  = base_score
  + recency_adjustment
  + ott_adjustment
  + quality_adjustment
  - negative_preference_penalty
  - repetition_penalty
```

`0.75/0.25`는 출발값일 뿐이다. 다음 ablation으로 확정한다.

```text
LightFM only
LightFM + ontology
LightFM + policies
LightFM + ontology + policies
```

### 9.4 부정 취향

- 동일 passed 영화는 hard exclude
- passed 영화와 장르만 같다는 이유로 강하게 제거하지 않는다.
- keyword, director, theme처럼 구체적인 반복 부정 관계에 더 높은 신뢰를 둔다.
- 부정 행동 개수와 일관성이 낮으면 penalty를 제한한다.
- negative penalty 총량은 base score의 일정 비율을 넘지 않게 한다.
- 최근 passed는 오래된 passed보다 강하게 반영한다.

### 9.5 OTT

`mode=all`:

- 구독 OTT streaming 가능 영화에 작은 bonus
- OTT가 없다는 이유만으로 탈락시키지 않음

`mode=subscribed_only`:

- 구독 OTT streaming 가능 여부를 hard filter
- 후보 부족 시 전체 영화 fallback 금지

OTT는 LightFM feature로 실험할 수 있지만 최종 정책 판단은 항상 최신 DB 값을 사용한다.

### 9.6 품질

품질은 개인 적합도를 대체하지 않는다.

- `vote_average`는 `vote_count` 신뢰도와 함께 사용
- popularity 단독 고득점 방지
- 품질 bonus 총량 제한
- 신규 영화는 낮은 vote count만으로 모두 제거하지 않도록 별도 처리

필요하면 Bayesian weighted rating을 사용한다.

### 9.7 단기 취향 변동

단기 취향은 기존 LightFM 후보의 가감점만으로 처리하지 않는다. 최근 positive 행동에서 concept profile을 만들고 별도 `short_term_context` retrieval을 수행한 뒤 장기 후보와 합친다.

```text
short_term_relevance(candidate)
  = sum(
      short_term_concept_score
      * candidate_edge_effective_strength
      * feature_type_weight
    )

drift_weight
  = min(MAX_DRIFT_WEIGHT, drift_confidence * MAX_DRIFT_WEIGHT)
```

초기 실험 범위:

```text
signal 없음                         0.00
최근 positive 영화 1개             0.10 ~ 0.20
서로 다른 영화의 concept 반복 확인 0.35 ~ 0.45
MAX_DRIFT_WEIGHT                    0.45
```

적용 원칙:

- source별 정규화 전 원시 점수를 서로 더하지 않음
- signal이 없거나 모두 감쇠됐으면 장기 기준선과 동일한 후보와 순위를 유지
- 한 번의 행동은 기존 장기 취향을 즉시 뒤집지 않고 약한 이동으로 처리
- 서로 다른 영화에서 같은 장르/theme/keyword 등이 반복되면 drift confidence를 높임
- actor 하나처럼 과도하게 넓거나 우연일 수 있는 concept은 IDF와 type cap 적용
- contextual 후보도 hard filter, OTT, 품질 최소 조건과 negative 취향을 통과해야 함
- passed는 positive contextual retrieval을 만들지 않고 동일 영화 제외와 negative evidence에 사용
- short-term 점수와 drift weight는 각각 기록하며 LightFM score로 위장하지 않음

장기 범죄 취향 사용자가 서로 다른 로맨스 영화에 최근 positive 행동을 반복하면 다음 동작을 보장한다.

1. 기존 LightFM 후보에 없던 로맨스 관련 영화가 `short_term_context` source로 생성된다.
2. eligible contextual 후보가 후보 100개에 포함된다.
3. 강한 drift에서는 최소 1개 이상의 고관련 contextual 후보가 최종 상위 20개에 진입한다.
4. 최근 행동이 24시간 밖으로 감쇠되면 순위가 장기 기준선 방향으로 복귀한다.

3번은 랜덤 노출 quota가 아니라 단기 취향 점수 결합이 실제 최종 순위에 영향을 주기 위한 동작 조건이다. 운영에서 고정 개수를 강제할지는 정확도 관측 후 결정한다.

### 9.8 다양성과 반복 감점

V3 1차에서는 ontology feature를 이용한 결정적 반복 감점과 MMR 재정렬을 적용한다.

```text
MMR(candidate)
  = relevance(candidate)
  - diversity_lambda * max_similarity(candidate, selected)
```

유사도 및 반복 feature:

- genre
- theme
- mood
- 주요 keyword
- director/actor

적용 원칙:

- 영화 ID 중복은 hard 제거
- 같은 creator나 concept의 과도한 반복만 bounded penalty 적용
- 높은 relevance 후보가 무관한 영화로 교체되지 않도록 penalty cap 적용
- 동일 입력과 config에서는 결과가 항상 같아야 함
- 장르/theme coverage, intra-list diversity와 정확도 손실을 함께 기록

### 9.9 탐색 [후속 구현]

V3 1차에서는 exploration 후보를 섞지 않는다. 정확도 기준선이 확정된 후 다음을 별도 실험한다.

- 랜덤 후보
- 신작
- long-tail
- 낮은 노출량 후보
- 최종 목록의 exploration 강제 quota

후속 실험에서도 eligibility와 강한 negative 취향 정책은 통과해야 한다.

### 9.10 후보 진단 및 정책 결정 기록

후보별 진단 결과는 계층별 객체를 합치지 않고 다음 구조로 기록한다.

```json
{
  "movie_id": 101,
  "candidate_sources": [
    {"source": "model", "source_rank": 12},
    {"source": "short_term_context", "source_rank": 4}
  ],
  "model_component": {
    "model_build_id": 31,
    "feature_registry_version": "v3.1",
    "raw_score": 2.31,
    "normalized_score": 0.84
  },
  "ontology_component": {
    "ontology_build_id": 17,
    "total_score": 0.60,
    "type_scores": {"genre": 0.18, "actor": 0.09, "theme": 0.21},
    "positive_path_ids": [901, 944],
    "negative_path_ids": []
  },
  "policy_component": {
    "policy_version": "v3.1",
    "total_effect": 0.01,
    "decisions": [
      {
        "policy_id": "ott_subscribed_bonus_v1",
        "effect": 0.05,
        "evidence": {"ott_id": 8}
      },
      {
        "policy_id": "negative_theme_penalty_v1",
        "effect": -0.04,
        "evidence": {"theme": "revenge"}
      }
    ]
  },
  "score_trace": {
    "personal_score": 0.80,
    "base_score": 0.75,
    "score_before_final_sort": 0.76,
    "rerank_effect": -0.05,
    "final_score": 0.71
  },
  "explanation": {
    "reason_codes": ["THEME_MATCH", "OTT_AVAILABLE"],
    "evidence_path_ids": [901],
    "attribution_type": "semantic_support_not_model_causality"
  }
}
```

규칙:

- `model_component`에는 LightFM 출력과 해당 artifact 식별자만 저장
- `ontology_component`에는 의미 일치 점수와 실제 graph path만 저장
- `policy_component`에는 정책별 가감점과 직접 근거만 저장
- `explanation`은 ontology path와 사용자에게 설명 가능한 정책만 참조
- `explanation`에서 `model_component`를 원인으로 표현하지 않음
- `score_trace`의 계산 합계가 실제 최종 정렬 점수와 일치해야 함

정책 성과를 관측할 때는 `policy_id`별 노출, saved, pinned, watched, passed 전환율을 집계한다. 초기에는 이 통계로 사람이 config를 조정하고, 자동 정책 변경은 하지 않는다.

---

## 10. Cold-start 설계

### 10.1 신규 사용자

사용자 상태별 경로:

```text
positive 행동 0개
-> onboarding + ontology rule + 품질 + 인기

positive 행동 1~4개
-> cold-start 중심 + LightFM 일부

positive 행동 5~9개
-> LightFM/ontology 혼합

positive 행동 10개 이상
-> LightFM 중심 + ontology/policy 재정렬
```

행동 개수 기준은 평가 후 조정한다.

초기 cold-start 점수 예시:

```text
cold_start_score
  = 0.45 * onboarding_genre_match
  + 0.25 * favorite_movie_semantic_match
  + 0.15 * quality
  + 0.15 * popularity
```

V1의 onboarding profile과 `dynamic_retriever.py` cold-start 흐름을 기준선으로 사용한다. V2에만 있는 ontology onboarding evidence가 동일 입력과 지표에서 개선을 보이면 해당 feature 계산만 선택적으로 추가한다.

### 10.2 신규 영화

- movie identity interaction이 없어도 ontology item feature로 LightFM 예측
- ontology feature가 없는 신규 영화는 품질/인기도 fallback
- 신규 영화는 ontology feature와 LightFM 예측 점수로 일반 후보에 포함
- 일정 상호작용이 쌓이면 일반 LightFM 후보로 자연스럽게 이동

### 10.3 모델 장애

fallback 순서:

```text
정상 V3 serving bundle
-> V3 정상 경로

V3 모델 없음/로드 실패 + 정상 ontology
-> ontology cold-start/policy 경로

ontology 없음 + V3 모델 정상
-> LightFM + 기본 정책

V3 전체 실패
-> 기존 정상 V1 또는 저장된 recommendations
```

---

## 11. 저장 구조와 진단

### 11.1 모델 build 기록

신규 `recommendation_model_builds` 테이블을 권장한다.

필수 컬럼:

```text
id
parent_model_build_id
model_version
algorithm
status
artifact_path
data_cutoff_at
ontology_build_id
config_snapshot
metrics
change_reason
changed_parameters
comparison_metrics
started_at
finished_at
error_message
is_active
```

최초 build의 `parent_model_build_id`, `change_reason`, `changed_parameters`, `comparison_metrics`는 nullable이다. 튜닝 build부터는 비교 기준 parent와 변경 내용을 필수로 기록한다.

### 11.2 Serving bundle 기록

`recommendation_serving_bundles` 권장 컬럼:

```text
id
model_build_id
ontology_build_id
policy_version
status
validation_metrics
created_at
activated_at
is_active
```

활성 bundle은 하나만 허용하고, 참조하는 model build와 ontology build 및 policy config가 모두 검증된 경우에만 원자적으로 교체한다. ontology build의 schema-scoped active 상태는 graph 관리용이며 실제 V3 요청이 사용할 조합은 이 bundle이 결정한다.

### 11.3 기존 snapshot 활용

1차 구현은 기존 테이블을 호환 목적으로 유지한다.

- `recommendation_runs`에 nullable `model_build_id` 추가
- `ontology_recommendations`에 nullable `model_build_id` 추가
- `source_scores`에 `model_component`, `ontology_component`, `policy_component`, `score_trace`를 분리 저장
- `explanation_tags`에는 사용자 노출 가능한 이유 code 저장

`model_component`와 `ontology_component`에 같은 feature 이름이 나타나더라도 서로 참조 관계를 만들지 않는다. model build의 입력 feature registry와 config는 build snapshot에서 확인하고, 후보의 추천 근거는 serving bundle에 고정된 ontology build의 evidence path에서 확인한다.

`ontology_recommendations`라는 이름은 V3 전체를 표현하기에는 부정확하지만, 초기 구현에서 테이블 rename으로 범위를 키우지 않는다. 필요하면 안정화 후 generic snapshot 테이블로 이전한다.

### 11.4 후보 stage

최소 stage:

```text
lightfm_candidate
merged_candidate
policy_ranked
final_response
```

전체 후보 100개의 무거운 설명 JSON을 모두 저장하지 않는다.

- 후보 전체: 계층별 숫자 점수, source, build/version 중심
- 상위 재정렬 대상: 정책 진단
- 최종 응답: 설명 경로와 최종 진단

feature별 튜닝 분석은 후보별 전체 path를 복제하지 않고 `type_scores`, reason code, source rank를 집계한다. 상세 path는 최종 응답과 진단 표본에서 ID 참조로 조회한다.

---

## 12. 코드 구조 계획

```text
app/api/v1/endpoints/
  # 기존 HTTP 경로, 요청/응답 계약 유지

app/services/recsys/
  contracts.py
  registry.py
  v1/adapter.py
  v2/adapter.py
  v3/adapter.py

app/services/recsys/v3/
  __init__.py
  config.py
  schemas.py
  model_store.py
  serving_bundle.py
  policy_registry.py
  lightfm_retriever.py
  recent_action_cache.py
  short_term_profile.py
  short_term_retriever.py
  candidate_merger.py
  ontology_analyzer.py
  score_normalizer.py
  policy_engine.py
  diversity_reranker.py
  cold_start.py
  explainer.py
  recommender.py

app/jobs/recsys/v3/
  __init__.py
  dataset_builder.py
  feature_builder.py
  trainer.py
  evaluator.py
  artifact_publisher.py
  candidate_materializer.py
  train_pipeline.py

app/crud/recsys/
  model_builds.py
  serving_bundles.py
  v3_training_data.py
  v3_candidates.py

app/models/
  recommendation_model_builds.py
  recommendation_serving_bundles.py

assets/ml_models/lightfm/
  README.md
  .gitkeep

assets/ml_models/v3/
  .gitkeep
```

`api/v1`은 HTTP API 버전이고 추천 엔진 버전이 아니다. 따라서 V3를 위해 `app/api/recsys/v3` 또는 별도 V3 endpoint를 만들지 않는다. 기존 endpoint는 URL, 요청 schema, 응답 schema를 유지한 채 공통 recommendation service registry에만 위임한다. `RECOMMENDATION_ENGINE` 해석, 엔진별 import, 최대 후보 수와 cold-start 동작 차이는 `app/services/recsys`의 registry/adapter가 책임진다.

의존 방향:

```text
recommender
  -> lightfm_retriever
  -> ontology_analyzer
  -> policy_engine
  -> diversity_reranker
  -> explainer

policy_engine -X-> lightfm 내부 객체
ontology_analyzer -X-> 정책 config
trainer -X-> FastAPI request context
```

---

## 13. 단계별 구현 계획

### Phase 0. 기준선 고정 및 dependency spike

작업:

- V1 정책별 불변 기준과 현재 config snapshot 보관
- V2 온톨로지의 `False`, `0.0`, deferred, 미사용 schema/config 항목을 전수 목록화하고 V3 사용 위치 결정
- 정책 registry에서 V1/V2/V3 신규 선택 근거를 기록
- Docker Python 버전 확인
- LightFM 설치, import, 소형 fit/predict 검증
- artifact mapping에 없는 user/item의 feature-only predict 가능 여부 검증
- NumPy/SciPy/scikit-learn/joblib 호환 버전 결정
- 학습과 serving process에서 artifact 저장/재로드 검증
- random seed 고정
- 현재 user 수와 eligible movie 수로 exact materialization 연산량·메모리 상한 계산

완료 기준:

- 컨테이너에서 최소 LightFM fit/predict/save/load 실행 성공
- mapped/unmapped user/item inference 지원 범위 확정
- 정확한 package pin 확정
- V1 기본 baseline과 V2 보조 비교 결과 보존
- V1 유지, V2 선택 채택, V3 신규 정책과 재조정할 점수 목록 확정
- 정의만 있고 사용 위치가 결정되지 않은 V3 feature 0개

### Phase 1. 학습 데이터 계약

작업:

- positive action extractor 작성
- passed/excluded extractor 작성
- LightFM user feature, long-term ontology profile, short-term profile schema 분리
- 중복 행동 집계 및 상한 정책 작성
- 최근성 sample weight 작성
- 사용자/영화 eligibility 통계 출력
- snapshot 데이터 한계를 진단 결과에 기록
- 시간 누수와 held-out 누수 검사 추가

완료 기준:

- 동일 DB snapshot에서 동일 sparse interaction 행렬 생성
- interaction 수, 사용자 수, 영화 수, 행동별 수량 검증
- passed가 WARP positive에 포함되지 않음
- 사용자 profile의 각 feature type이 어떤 원천과 계층에서 사용되는지 registry로 검증

### Phase 2. 온톨로지 feature exporter

작업:

- 현재 full graph build 약 60분을 성능 기준선으로 기록
- V2/V3 build의 `schema_version`별 active 상태와 source hash 분리 migration
- V3 ontology build에서 actor node와 `has_actor` edge 활성화
- overview signal extractor 실행과 `has_theme`/`has_mood` edge materialization
- graph build와 LightFM 재학습 trigger 분리
- 변경 없는 ontology 입력은 full build skip
- 추천 가능 catalog 기준과 평가 coverage 검증
- staging/bulk build 및 stage별 elapsed/rows-per-second 기록
- 활성 ontology build에서 item feature 추출
- genre/keyword/director/actor/theme/mood/OTT namespace 구성
- feature frequency와 cardinality report 작성
- relation weight/confidence 반영
- ontology build ID와 mapping hash 저장
- source row, graph node/edge, sparse matrix nnz를 feature별로 기록

완료 기준:

- 동일 build에서 deterministic feature matrix 생성
- 모든 eligible 영화의 feature 유무 통계 출력
- 신규/희소 영화가 feature로 표현되는지 검증
- actor 원천 row가 있으면 actor node/edge와 item feature nnz가 모두 0보다 큼
- overview signal이 있으면 overview source의 theme/mood edge와 item feature nnz가 모두 0보다 큼
- 필수 feature가 비활성화된 build는 정상 V3 model 학습 대상으로 승인하지 않음
- actor/evidence 포함 최초 V3 full build 60분 이내 no-regression
- 동일 source build skip과 overview 증분 추출 검증
- 최적화 전후 build 시간과 graph 크기 보고
- V3 build 활성화가 V2 active build를 비활성화하지 않음

### Phase 3. LightFM baseline

작업:

- popularity baseline 구현
- identity-only LightFM 구현
- WARP hyperparameter 최소 탐색
- 사용자별 top-N predict 구현
- movie block 단위 exact top-K와 peak memory 측정
- candidate Recall과 순위 지표 계산

완료 기준:

- 모델 학습, 저장, 재로드 후 예측 동일
- popularity 대비 LightFM 결과 비교 가능
- 사용자별 predict 시간 기록
- 전체 dense user×movie score matrix를 만들지 않고 top-100 생성
- exact 방식이 목표 주기를 넘으면 ANN/MIPS spike 필요 여부 결정

### Phase 4. Hybrid LightFM

작업:

- `recommendation_model_builds`와 `recommendation_serving_bundles` migration/CRUD 구현
- ontology item feature 연결
- onboarding user feature 연결
- actor 및 overview-derived theme/mood feature가 model input에 포함되는지 확인
- identity feature 유지
- identity-only와 hybrid ablation
- 신규 사용자/영화 score 검증
- 최종 artifact manifest와 publisher 구현
- 검증 전 serving bundle 후보를 inactive 상태로 생성
- model build에 parent build, 변경 사유, 변경 parameter 기록
- 동일 split/seed의 build 비교 report 생성

완료 기준:

- hybrid 모델이 실제 ontology feature matrix를 사용
- 신규 영화가 ontology feature만으로 score를 받을 수 있음
- feature registry의 필수 model feature가 학습 행렬과 artifact mapping에 존재
- 활성 artifact 원자적 교체 및 이전 모델 fallback 검증
- inactive bundle 생성이 현재 active bundle에 영향 주지 않음
- tuning 전후 build의 config diff와 비교 metric을 재현 가능

### Phase 5. 후보 materialization

작업:

- 전체 대상 사용자 top-N batch 계산
- user/movie block과 checkpoint 기반 재개 구현
- `recommendations` 저장 구조에 model source와 점수 기록
- 유저별 원자적 교체
- 최소 후보 수 검증 실패 시 기존 결과 유지
- scheduler를 V3 train/materialize pipeline에 연결

완료 기준:

- 사용자별 후보 중복 0
- 실패 사용자 기존 후보 유지
- 전체/1명당 실행 시간과 source 통계 출력
- peak RSS와 `users_per_second` 출력

### Phase 6. 온톨로지 분석 계층

작업:

- LightFM user feature와 long-term ontology profile 구현
- V1 profile 행동 의미를 V3 장기 profile로 옮기고 V2 graph query 형태만 `ontology_analyzer`에서 참고
- actor/director와 asset/overview theme/mood provenance 유지
- 장기/단기 병합 후 선정된 최대 100개 후보만 의미 분석
- type별 positive/negative score 계산
- path evidence 생성
- 전체 후보에 대한 무거운 JSON 집계 방지

완료 기준:

- LightFM score 변경 없이 ontology evidence를 독립 생성
- 후보별 type score 합계 검증
- explanation path가 실제 graph edge를 참조
- actor 및 overview 입력에서 profile과 candidate evidence가 0보다 큼
- feature별 source-to-profile-to-candidate coverage report 생성

### Phase 7. 단기 취향 후보 생성

작업:

- V1 recent action cache를 timestamp 포함 versioned JSON event로 확장
- saved/pinned/watched/passed 최신 상태와 blacklist 동기화
- recent event에서 short-term positive/negative concept profile 생성
- concept 반복도, 행동 strength, 시간 감쇠로 drift confidence 계산
- V1 dynamic retriever를 `short_term_context` 독립 source로 분리
- LightFM 장기 후보와 contextual 후보의 source별 점수 정규화
- drift weight 혼합과 강한 drift의 contextual source floor 적용
- 후보 100개 병합, 중복 제거, source/component 진단 기록
- Redis 장애 시 장기 후보 fallback
- concept→movie set-based query와 실제 크기 표본의 `EXPLAIN (ANALYZE, BUFFERS)` 검증

완료 기준:

- recent positive signal이 있으면 기본 후보 coverage와 무관하게 contextual retrieval 실행
- 장기 범죄/최근 로맨스 조건에서 기존 장기 후보에 없던 로맨스 후보 생성
- 강한 drift 조건에서 eligible contextual 후보가 후보 100개와 최종 상위 20개에 진입
- signal 없음과 감쇠 완료 상태에서 장기 기준선 결과 유지
- passed만으로 positive contextual 후보를 생성하지 않음
- 동일 입력의 후보와 drift score가 deterministic
- 사용자 1명당 profile/retrieval/merge 실행 시간 기록
- 전체 graph scan과 후보별 N+1 query 0

### Phase 8. 정책 엔진

작업:

- V1 정책 불변 조건을 V3 policy interface에 연결
- 선택 채택한 V2 정책의 source decision registry 연결
- hard filter
- score normalization
- ontology blend
- recent behavior
- negative preference
- OTT
- quality
- genre/theme/mood/creator 반복 감점
- session 동일 영화 중복 방지
- model/ontology/policy/score trace를 분리한 후보 진단 schema 구현
- 정책별 effect와 evidence 기록
- explanation이 ontology path와 설명 가능한 정책만 참조하도록 validator 구현

완료 기준:

- hard policy 위반 0
- V1에서 계승한 제외/OTT/보충/원자적 교체 불변 조건 충족
- 정책 effect 합계와 최종 score 일치
- LightFM score와 ontology reason path 사이에 인과 관계를 표시하는 필드가 없음
- evidence가 없으면 허위 explanation을 생성하지 않음
- 정책 on/off ablation 가능
- 단일 정책이 전체 모델 점수를 압도하지 않도록 cap 검증
- 반복 감점 전후 정확도와 다양성 지표 비교

### Phase 9. 다양성과 cold-start

작업:

- ontology feature 기반 MMR
- genre/theme/mood/actor/director 반복 감점
- 신규 사용자 상태별 blending
- 신규 영화 ontology/LightFM 후보 경로
- V3 모델/ontology 장애 fallback

완료 기준:

- 최종 추천 중복 0
- 반복 감점과 MMR 결과가 deterministic
- 정확도 손실과 다양성 변화 기록
- 행동 0개 사용자 추천 가능
- 모델 없이도 cold-start fallback 가능

### 후속 Phase. Exploration [V3 1차 이후]

선행 조건:

- V3 정확도 기준선과 정책 가중치 확정

후속 작업:

- 랜덤 후보 source
- 신작/long-tail/낮은 노출량 source
- exploration quota
- 정확도 손실 상한과 발견성 향상량 평가

이 단계는 V3 1차 완료 조건에 포함하지 않는다.

### Phase 10. API 통합

작업:

- `RECOMMENDATION_ENGINE=v3` 분기 추가
- 기존 V1/V2 분기 유지
- API 응답 contract 유지
- model build와 ontology build를 run/snapshot에 연결
- model/ontology/policy 호환성 validator와 serving bundle 원자적 활성화
- shadow mode 추가 검토

완료 기준:

- 동일 endpoint에서 설정만으로 V1/V2/V3 전환
- V3 실패 시 정의된 fallback
- 요청별 model/ontology/policy version 확인 가능
- bundle 활성화 실패 시 직전 정상 bundle 유지

### Phase 11. Rollout

작업:

- V3 serving bundle을 비활성 상태로 배포
- 제한된 사용자부터 단계적으로 활성화
- 오류율, latency, fallback 비율 관측
- 이상 발생 시 직전 정상 bundle로 rollback

완료 기준:

- V1/V2 제거 없이 V3 활성화 가능
- bundle 단위 활성화와 rollback 가능

---

## 14. 구현 중 결정해야 할 항목

다음은 코드 작성 전에 수치 또는 운영 방식을 확정해야 한다.

- production positive 행동의 상대 weight
- 중복 positive 행동 집계 방식과 cap
- LightFM loss별 실험 범위
- latent component 수, epoch, regularization
- feature 최소 빈도와 actor 수 제한
- `CANDIDATE_POOL_SIZE=100`을 채우기 위한 source별 내부 over-retrieval 배수
- short-term positive 행동별 상대 strength와 recent time bucket
- drift confidence 계산식과 weak/strong 경계
- `MAX_DRIFT_WEIGHT`와 strong drift contextual source floor
- LightFM/ontology blend 비율
- 정책 adjustment 최대 비율
- MMR lambda
- exploration 목표 비율
- cold-start에서 LightFM으로 전환하는 행동 개수
- 모델 재학습 주기와 candidate materialization 주기
- 불변 행동 event log를 새 테이블로 둘지 여부

이 값들은 코드에 흩어 놓지 않고 V3 config와 recommendation run snapshot에 저장한다.

---

## 15. 주요 위험과 대응

### 15.1 실제 협업 데이터 부족

사용자나 positive interaction이 너무 적으면 LightFM identity-only가 의미 있는 협업 패턴을 학습하지 못한다.

대응:

- 학습 전 density와 사용자/영화별 interaction 분포 출력
- identity-only와 ontology hybrid 성능 분리
- 데이터가 부족한 동안 cold-start/hybrid 비중 유지
- LightFM 도입 자체를 성능 향상으로 간주하지 않음

### 15.2 평가 누수

held-out 정답 영화가 사용자 feature 또는 interaction에 다시 포함되면 지표가 왜곡된다.

대응:

- split 이후 train profile과 test label 생성
- 평가 user feature에 test 행동을 넣지 않음
- mapping 생성과 feature 생성은 허용하되 test interaction은 학습하지 않음
- split hash와 seed 저장

### 15.3 점수 스케일 충돌

LightFM raw score와 정규화되지 않은 rule score를 바로 더하면 정책이 모델을 덮어쓸 수 있다.

대응:

- 사용자 후보 내 정규화
- 계층별 점수 범위와 cap
- score component 합계 검사
- 정책 on/off ablation

### 15.4 설명 과장

LightFM이 학습한 이유와 온톨로지의 의미 연결을 동일한 것으로 표현할 위험이 있다.

대응:

- 설명은 실제 ontology evidence와 정책만 사용
- `model_score`와 `reason_paths` 분리
- 근거가 없으면 일반적인 추천 이유를 생성하지 않음

### 15.5 artifact와 ontology build 불일치

학습 당시 feature mapping과 serving ontology가 다르면 score와 설명이 일치하지 않을 수 있다.

대응:

- artifact에 `ontology_build_id` 저장
- model feature는 학습 당시 mapping을 사용
- model/ontology/policy version을 serving bundle로 묶어 원자적으로 활성화
- bundle의 ontology build로 의미 분석하고 최신 OTT availability만 원본 DB에서 재확인
- schema/feature가 바뀐 새 ontology build는 호환 모델이 생성되기 전까지 serving에 단독 연결하지 않음
- schema-compatible evidence만 갱신하는 예외가 필요하면 별도 compatibility validator와 run 기록 필수

### 15.6 LightFM 유지보수 및 빌드

LightFM은 native extension 빌드가 필요하고 최신 Python/NumPy 조합에서 설치 문제가 발생할 수 있다.

대응:

- Phase 0 dependency spike를 구현 선행 조건으로 둠
- Docker에서 검증한 버전 pin
- 모델 계층을 interface로 감싸 향후 다른 retriever로 교체 가능하게 유지

### 15.7 전체 Catalog Top-K 비용

사용자별로 약 117만 영화 전체를 score하면 dense 결과 저장 여부와 무관하게 연산량이 커질 수 있다.

대응:

- 시작 전 `users * eligible_movies` 규모와 예상 시간을 출력
- blockwise exact top-K로 dense matrix 메모리 생성을 금지
- batch checkpoint와 이전 정상 후보 유지
- exact 처리량이 materialization 주기를 충족하지 못하면 ANN/MIPS를 별도 spike
- 근사 검색은 exact top-K 대비 `Recall@100` 기준 통과 후에만 채택

### 15.8 온라인 Graph 조회 비용

단기 취향 후보와 설명을 위해 1,200만 edge를 요청마다 넓게 조인하면 V2에서 해결한 실행 시간 문제가 다시 발생할 수 있다.

대응:

- concept→movie composite index와 bounded top-K query
- profile concept를 한 번에 전달하고 후보별 N+1 query 금지
- 장기 profile materialization/cache와 short-term delta 분리
- 후보 최대 100개에 대해서만 상세 evidence 조회
- query plan, p50/p95, returned/scanned row 비율을 성능 기준으로 저장

### 15.9 Source 정규화 불안정

후보 수가 적거나 점수가 모두 같은 source에 percentile/z-score를 적용하면 작은 변화가 순위를 과장할 수 있다.

대응:

- source별 raw 후보 집합에서만 normalization fit
- 단일/동점 source는 중립값과 deterministic tie-break 사용
- normalization method와 통계를 run snapshot에 기록
- source on/off ablation에서 순위 급변 여부 확인

---

## 16. 구현 착수 Gate

다음 조건을 확인한 뒤 본 구현에 들어간다.

1. V1 추천 정책 baseline과 config snapshot 보존
2. V1/V2/V3 신규 정책별 source decision registry 초안 작성
3. Docker Python 3.11에서 LightFM fit/predict/save/load spike 통과
4. unmapped user/item feature-only inference 지원 범위 확정
5. V2/V3 ontology build의 schema-scoped activation migration 설계 확정
6. exact blockwise top-K 소형 benchmark와 전체 규모 예상 시간 산출
7. 온라인 concept→movie query의 필수 index와 query plan 확인
8. 현재 dirty worktree의 사용자 변경을 보존하고 V3 변경 범위 분리

Gate에서 실패한 항목은 fallback 또는 별도 spike 결론을 문서화한 뒤 다음 Phase로 이동한다.

---

## 17. 최종 구현 원칙

```text
LightFM은 장기 후보를 학습한다.
온톨로지는 LightFM feature, 단기/cold-item 후보와 의미 evidence를 제공한다.
정책 엔진은 서비스상 최종 순위를 결정한다.
Explainer는 실제 의미 근거만 사용자에게 보여준다.
```

V3의 성공은 LightFM을 추가했다는 사실이 아니라 다음을 증명하는 데 있다.

1. 협업 학습이 기존 추천보다 후보 회수율을 높였는가.
2. 온톨로지 feature가 희소 사용자와 희소 영화에 실제로 도움이 되었는가.
3. 온톨로지 분석이 추천 근거와 부정 취향 판단을 개선했는가.
4. 정책 엔진이 품질, OTT와 반복 완화 목적을 정확도 손실을 통제하며 달성했는가.
5. 각 계층의 효과를 독립적으로 측정하고 재현할 수 있는가.

이 다섯 항목을 단계별 ablation과 동일한 데이터 snapshot으로 확인한 뒤 V3를 기본 엔진으로 전환한다.

---

## 참고

- LightFM 공식 프로젝트: https://github.com/lyst/lightfm
- LightFM Dataset 공식 문서: https://making.lyst.com/lightfm/docs/lightfm.data.html
- LightFM 모델 및 loss 공식 문서: https://making.lyst.com/lightfm/docs/lightfm.html
