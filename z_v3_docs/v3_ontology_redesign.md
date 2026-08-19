# V3 온톨로지 그래프 점검 및 재정의안

## 0. 결론

V2 온톨로지의 기본 개념과 원천 데이터는 재사용할 수 있지만, V3에서는 그래프를 그대로 복제하지 않고 재정의하는 편이 적절하다.

재사용:

- movie, genre, keyword, people, OTT 원천 데이터
- theme 30개와 mood 16개 controlled vocabulary
- genre/keyword에서 theme/mood로 연결하는 수동 asset
- overview semantic signal 추출 결과
- build 단위 version 관리 개념

재정의:

- actor/director를 하나의 `person` node로 통합하고 relation으로 역할 구분
- OTT streaming/rent/buy 관계 분리
- canonical semantic edge와 원천 evidence 분리
- theme/mood 관계의 실제 사용 위치 명시
- overview signal을 build 완료 후 추가하지 않고 build 안에서 materialize
- LightFM feature용 그래프와 설명용 전체 그래프의 cardinality 정책 분리
- ontology evidence와 LightFM model attribution의 명시적 분리
- 허용된 multi-hop 경로와 감쇠를 명시적으로 제한

기존 V2 build와 테이블 데이터를 삭제하거나 덮어쓰지 않는다. V3 schema version을 가진 새 ontology build를 생성하고 V2/V3를 병행 검증한다.

---

## 1. 점검 범위

확인한 대상:

- `app/services/recsys/v2/graph_builder.py`
- `app/services/recsys/v2/candidate_generator.py`
- `app/jobs/recsys/v2/overview_signal_extractor.py`
- `app/jobs/recsys/v2/materialize_overview_edges.py`
- `app/jobs/recsys/v2/validate_assets.py`
- `app/models/ontology.py`
- `assets/ontology/*.json`
- 활성 ontology build의 node/edge/source 분포

점검 시점의 활성 build:

```text
build_id       3
engine_version v2.0.0
node_count     3,880,321
edge_count     12,305,360
```

활성 build는 config 기본값과 달리 다음 option으로 생성됐다.

```json
{
  "include_actor_nodes": true,
  "include_actor_edges": true,
  "include_overview_derivation": false
}
```

따라서 현재 활성 그래프에는 actor가 실제로 존재한다. 문제는 코드 기본값이 `False`라서 재빌드 방식에 따라 actor가 다시 빠질 수 있다는 점이다.

overview edge는 build option이 `false`인 상태로 build가 활성화된 뒤 별도 materialization worker로 추가됐다.

---

## 2. 현재 그래프 현황

### 2.1 Node

| node type | count |
| --- | ---: |
| movie | 1,176,546 |
| actor | 2,240,477 |
| director | 377,268 |
| keyword | 85,956 |
| genre | 19 |
| theme | 30 |
| mood | 16 |
| OTT | 9 |

### 2.2 주요 Edge

| relation | edge count | 연결된 movie 수 |
| --- | ---: | ---: |
| `has_actor` | 7,311,782 | 800,807 |
| `has_director` | 980,733 | 980,733 |
| `has_genre` | 1,323,165 | 860,001 |
| `has_keyword` | 1,105,271 | 336,031 |
| `has_theme` | 658,016 | 447,990 |
| `has_mood` | 873,659 | 586,159 |
| `available_on` | 52,676 | 26,390 |

### 2.3 Semantic asset

```text
theme definitions 30
mood definitions  16

genre -> theme 9
genre -> mood  9
keyword -> theme 16
keyword -> mood  4
theme related_to theme 10
theme evokes mood       6
mood compatible_with    4
```

validator에는 `broader_than`, `narrower_than`도 정의되어 있지만 현재 asset에는 해당 relation이 없다.

### 2.4 Overview signal

```text
theme signal 352,424개 / 277,597 movies
mood signal   95,549개 /  88,130 movies
전체 signal  447,973개
```

현재 extractor는 controlled vocabulary의 key, 한글/영문 label, alias를 overview에서 정규식으로 찾는다.

---

## 3. 확인된 문제

### 3.1 Build option에 따라 핵심 관계가 사라질 수 있음

V2 코드 기본값:

```text
ENABLE_ACTOR_NODES_IN_GRAPH_BUILD = False
ENABLE_ACTOR_EDGES_IN_GRAPH_BUILD = False
ENABLE_OVERVIEW_DERIVATION_IN_GRAPH_BUILD = False
```

활성 build는 actor를 수동 option으로 켰지만, 동일 option을 빠뜨린 재빌드에서는 actor가 조용히 사라진다.

V3 정상 build에서는 필수 relation을 optional boolean 기본값으로 두지 않는다.

### 3.2 Actor와 director node가 같은 사람을 중복 표현

원천 DB는 같은 `people.id`를 actor와 director에 사용한다. V2 그래프는 이를 `actor:{id}`, `director:{id}` 두 node type으로 나눈다.

문제:

- 동일 인물의 정체성이 분리됨
- 배우이면서 감독인 인물의 관계를 연결하기 어려움
- node와 embedding feature가 중복될 수 있음

V3에서는 `person:{people.id}` 하나로 통합하고 relation으로 역할을 구분한다.

```text
movie -has_actor-> person
movie -has_director-> person
```

사용자 profile에서는 역할별 점수를 계속 분리한다.

```text
preferred_actor_scores[person_id]
preferred_director_scores[person_id]
```

### 3.3 Actor feature cardinality가 매우 큼

| feature | 전체 ID | 영화 1편만 연결 | 2편 이상 | 5편 이상 |
| --- | ---: | ---: | ---: | ---: |
| actor | 2,240,477 | 1,528,701 | 711,776 | 263,755 |
| director | 377,268 | 237,149 | 140,119 | 40,678 |
| keyword | 68,875 | 31,162 | 37,713 | 21,390 |

전체 actor를 LightFM item feature로 넣으면 sparse matrix 차원과 모델 embedding이 불필요하게 커진다. 영화 한 편에만 등장한 actor는 다른 영화로 일반화할 수 없어 hybrid feature의 이점도 거의 없다.

V3 원칙:

- ontology graph와 explanation에는 전체 관계 보존
- LightFM feature exporter에서만 빈도/coverage 기준 적용
- 최초 실험은 `movie_frequency >= 5`
- `>= 2`, `>= 5`, `>= 10`을 ablation으로 비교
- runtime profile은 low-frequency actor도 evidence로 사용할 수 있지만 점수 cap 적용
- keyword는 최소 빈도와 최대 catalog 비율을 함께 적용

### 3.4 OTT relation이 제공 방식을 잃음

원천 `movie_otts`에는 다음 값이 있다.

```text
is_streaming
is_rent
is_buy
```

현재 graph는 모두 `available_on` 하나로 저장한다. 실제 원천 분포는 다음과 같다.

```text
전체       52,676
streaming  35,018
rent       20,956
buy        17,409
```

한 row가 여러 제공 방식을 가질 수도 있지만, 현재 edge만으로는 `subscribed_only`에 필요한 streaming 여부를 알 수 없다.

V3 relation:

```text
movie -available_streaming_on-> ott
movie -available_rent_on-> ott
movie -available_buy_on-> ott
```

최종 serving 정책은 여전히 최신 `movie_otts` DB를 다시 확인한다. 그래프 relation은 학습 feature, 의미 evidence와 snapshot 설명에 사용한다.

### 3.5 Semantic evidence가 canonical edge 하나에 덮어써짐

현재 unique key:

```text
(build_id, source_node_id, target_node_id, relation_type)
```

같은 영화와 theme/mood 사이에 다음 evidence가 동시에 존재할 수 있다.

```text
genre rule
keyword rule
overview signal
```

하지만 `has_theme` 또는 `has_mood` canonical edge는 하나만 저장된다.

현재 동작:

- genre와 keyword가 같은 concept을 만들면 먼저 insert된 경로만 남을 수 있음
- asset-derived edge와 overview edge가 겹치면 weight와 confidence는 각각 `GREATEST`
- properties는 overview properties로 교체됨
- source 문자열은 `derived+overview_signal`이어도 properties에는 overview 정보만 남음
- 서로 다른 evidence의 최대 weight와 최대 confidence를 조합하면 실제로 존재하지 않은 조합이 만들어질 수 있음

genre/keyword derivation만 확인해도:

```text
canonical semantic target 1,126,295개
여러 경로를 가진 target      5,183개
genre와 keyword가 모두 기여  5,149개
```

수량은 전체 대비 작지만, 추천 설명과 점수 provenance가 정확하지 않다는 구조적 문제가 있다.

### 3.6 정의된 semantic relation 일부가 추천에서 사용되지 않음

현재 candidate query가 읽는 relation:

```text
has_genre
has_keyword
has_actor
has_director
has_theme
has_mood
```

asset에는 있지만 후보 생성이나 profile 확장에서 사용되지 않는 relation:

```text
related_to
evokes_mood
compatible_with
broader_than
narrower_than
```

`implies_theme`, `implies_mood`만 build 시 movie semantic edge 파생에 사용된다.

V3에서는 relation을 추가하는 것만으로 구현 완료로 보지 않고, relation registry에 실제 consumer를 지정한다.

### 3.7 활성 build가 생성 후 변경됨

활성 build 3은 다음 순서로 만들어졌다.

```text
actor 포함, overview 제외 상태로 success/active
-> 별도 worker가 overview edge 447,973개 materialize
-> 기존 활성 graph와 build properties 변경
```

문제:

- 동일 build ID의 내용이 시간에 따라 달라짐
- LightFM artifact가 어느 graph 상태를 사용했는지 재현하기 어려움
- source hash가 post-build overview 변경을 대표하지 못함

V3 build는 활성화 후 immutable이어야 한다.

### 3.8 Source hash가 DB graph 입력 전체를 대표하지 않음

현재 기본 source payload는 engine version과 JSON asset hash 중심이다.

빠질 수 있는 정보:

- actor/overview option이 명시되지 않았을 때의 실제 default
- overview extractor version과 signal snapshot
- movie/mapping table의 build 시점 상태
- relation schema version
- semantic aggregation policy version

V3 manifest에 이 값을 포함한다.

### 3.9 Build 시간 관측이 불완전함

활성 build 3은 `status=success`지만 `finished_at`이 `NULL`이라 DB 기록만으로 정확한 graph build 시간을 계산할 수 없다. stage별 시작/종료 시간과 처리량도 저장되지 않는다.

실제 실행 경험 기준 full graph build 시간은 약 1시간이다. 이를 V3 최적화 baseline으로 사용한다.

현재 graph 규모:

```text
node 약 388만
edge 약 1,230만
actor edge 약 731만
```

actor가 전체 edge의 가장 큰 비중을 차지하므로 V3에서 actor와 evidence를 모두 활성화하면 관측과 최적화 없이 build 시간이 더 늘어날 수 있다.

---

## 4. V3 Node 재정의

### 4.1 필수 Node

| node type | ref_id | 역할 |
| --- | --- | --- |
| movie | `movies.id` | 추천 대상 |
| genre | `genres.id` | 기본 콘텐츠 분류 |
| keyword | `keywords.id` | 세부 내용 feature |
| person | `people.id` | actor/director 통합 인물 |
| theme | asset key | 서사 주제 |
| mood | asset key | 감정 및 분위기 |
| ott | `otts.id` | 제공 플랫폼 |

### 4.2 V3 1차에서 추가하지 않을 Node

다음은 LightFM metadata feature로 실험할 수 있지만 ontology node로 바로 추가하지 않는다.

- language
- release decade
- runtime bucket
- popularity bucket
- vote quality bucket

단순 수치나 bucket까지 모두 node로 만들면 그래프 의미가 약해지고 관리 대상만 늘어난다. 실제 semantic relation이나 설명 요구가 생길 때 추가한다.

### 4.3 User는 Graph Node로 저장하지 않음

V2 원칙을 유지한다.

- 영화 온톨로지는 공통 immutable build
- 사용자 취향은 요청 또는 materialization 시 runtime profile
- LightFM user embedding은 model artifact
- short-term profile은 Redis

---

## 5. V3 Relation 재정의

### 5.1 Factual Relation

| source | relation | target | 사용 위치 |
| --- | --- | --- | --- |
| movie | `has_genre` | genre | LightFM, profile, evidence, 다양성 |
| movie | `has_keyword` | keyword | LightFM, profile, evidence |
| movie | `has_actor` | person | LightFM 선별, profile, evidence |
| movie | `has_director` | person | LightFM 선별, profile, evidence |
| movie | `available_streaming_on` | OTT | LightFM 선택, OTT 정책 |
| movie | `available_rent_on` | OTT | 설명 및 향후 정책 |
| movie | `available_buy_on` | OTT | 설명 및 향후 정책 |

Factual relation의 graph strength는 기본적으로 `1.0`이다. actor가 추천에서 director보다 약해야 한다는 정책은 graph fact weight가 아니라 model 학습과 ontology scorer config에서 처리한다.

### 5.2 Semantic Derivation Relation

V2의 `implies_*`는 논리적 필연처럼 보이므로 V3에서는 `suggests_*`로 이름을 명확히 한다.

| source | relation | target | 소비자 |
| --- | --- | --- | --- |
| genre | `suggests_theme` | theme | semantic build |
| genre | `suggests_mood` | mood | semantic build |
| keyword | `suggests_theme` | theme | semantic build |
| keyword | `suggests_mood` | mood | semantic build |
| theme | `evokes_mood` | mood | semantic build 및 explanation |

이 relation은 요청마다 전체 graph를 순회하기보다 build 시 canonical movie concept을 만드는 evidence로 사용한다.

```text
movie -has_genre-> genre -suggests_theme-> theme
=> movie -has_theme-> theme evidence
```

### 5.3 Canonical Movie Semantic Relation

```text
movie -has_theme-> theme
movie -has_mood-> mood
```

canonical edge는 여러 evidence를 합친 조회용 결과다.

evidence source family:

```text
genre_rule
keyword_rule
overview_signal
theme_to_mood_rule
```

### 5.4 Concept Relation

| source | relation | target | 사용 위치 |
| --- | --- | --- | --- |
| theme | `related_to` | theme | ontology analyzer 1-hop 확장 |
| theme | `broader_than` | theme | sparse/cold-start backoff |
| theme | `narrower_than` | theme | explanation 및 의미 확장 |
| theme | `evokes_mood` | mood | semantic evidence |
| mood | `compatible_with` | mood | session 의미 분석과 다양성 |

관계 방향:

- `related_to`, `compatible_with`는 symmetric relation으로 validator와 builder가 역방향을 보장
- `broader_than`, `narrower_than`은 inverse pair를 보장
- `evokes_mood`는 단방향

현재 `broader_than`, `narrower_than` asset은 0개다. 실제 asset이 추가되기 전에는 active relation registry에서 비활성으로 표시하며 점수식에 넣지 않는다.

---

## 6. Semantic Edge와 Evidence 분리

### 6.1 권장 저장 구조

기존 `ontology_edges`는 canonical relation 조회에 유지한다.

추가:

```text
ontology_edges.effective_strength
ontology_edge_evidence
```

`ontology_edge_evidence` 권장 컬럼:

```text
id
build_id
edge_id
evidence_type
source_ref
path
raw_weight
confidence
effective_strength
properties
created_at
```

unique key 예시:

```text
(build_id, edge_id, evidence_type, source_ref)
```

direct factual edge는 별도 evidence row가 없어도 된다. 여러 원천이 합쳐지는 `has_theme`, `has_mood`에 evidence row를 필수로 둔다.

### 6.2 결합 방식

evidence별 기본값:

```text
evidence_strength = raw_weight * confidence
```

상관된 evidence가 무제한 합산되지 않게 source family 내부에서는 최댓값을 사용한다.

```text
family_strength = max(evidence_strength in same family)
```

서로 다른 family는 bounded union으로 결합한다.

```text
effective_strength
  = 1 - product(1 - family_strength)
```

장점:

- 항상 `[0, 1]`
- evidence가 늘수록 강해지지만 선형 폭증하지 않음
- genre와 keyword와 overview가 각각 기여 가능
- 설명 시 실제 evidence path를 보존

이 결합식도 실험 대상이며 `aggregation_policy_version`을 build manifest에 저장한다.

### 6.3 Overview 신뢰도

현재 overview extractor는 exact term/alias matching이므로 다음을 적용한다.

- overview evidence를 factual edge와 동일하게 취급하지 않음
- 수동 표본 precision 검증
- noisy alias와 다의어 목록 지속 관리
- `extractor_version`별 coverage와 precision 기록
- 낮은 confidence signal은 canonical edge에서 제외하거나 약하게 반영
- matched term과 overview hash를 evidence에 보존

overview feature를 사용하되 정확도 검증 없이 높은 가중치를 부여하지 않는다.

### 6.4 LightFM Attribution과 분리

ontology evidence는 후보와 사용자 취향 사이에 현재 존재하는 의미 관계다. LightFM 학습에 같은 ontology feature가 포함됐더라도 이 evidence를 LightFM 예측의 인과적 feature contribution으로 저장하거나 표시하지 않는다.

```text
model component
  model_build_id, raw/normalized score, feature registry version

ontology component
  ontology_build_id, type score, evidence path

explanation
  ontology evidence path와 설명 가능한 policy만 참조
```

사람은 feature 유형별 model rank와 ontology evidence 및 실제 행동 성과를 집계해 튜닝 가설을 세울 수 있다. 그러나 특정 feature가 LightFM 점수의 원인이었다는 결론은 feature ablation과 재학습 build 비교 없이 내리지 않는다.

---

## 7. Multi-hop 사용 규칙

V3는 graph를 사용하지만 unrestricted traversal을 하지 않는다.

### 7.1 허용 경로

직접 취향:

```text
profile -> genre/keyword/person/theme/mood <- movie
```

최근 행동 기반 후보 retrieval:

```text
recent positive movie -> genre/keyword/person/theme/mood <- candidate movie
```

이 경로는 LightFM 장기 후보를 재정렬하기 위한 조회에만 제한하지 않는다. 유효한 최근 positive 행동이 있으면 해당 concept에 연결된 영화를 별도 `short_term_context` 후보로 조회한다. 따라서 장기 후보에 없는 영화도 단기 후보가 될 수 있으며, 요청 시 기존 graph snapshot을 읽을 뿐 graph를 다시 build하거나 수정하지 않는다.

build-time semantic derivation:

```text
movie -> genre/keyword -> theme/mood
movie -> theme -> mood
```

request-time 1-hop 확장:

```text
profile theme -> related theme <- candidate movie
profile mood -> compatible mood <- candidate movie
```

### 7.2 금지

- 방문 node 수 제한 없는 탐색
- theme `related_to` 반복 순회
- mood `compatible_with` 반복 순회
- cycle을 따라 동일 점수를 재누적
- LightFM feature에 무제한 파생 feature 추가

### 7.3 감쇠

초기 원칙:

```text
direct match              1.0
canonical semantic match  edge effective_strength
related theme 1-hop       direct score * relation strength * hop decay
compatible mood 1-hop     short-term/diversity에서 제한적으로 사용
```

정확한 hop decay는 V3 ablation으로 정한다. 모든 multi-hop contribution에는 후보별 상한을 둔다.

V3 1차에서는 이 graph relation을 MMR과 genre/theme/mood 반복 감점에도 사용한다. 랜덤·신작·long-tail 후보를 별도로 섞는 exploration은 정확도 기준선 확정 후로 미룬다.

단기 후보 retrieval도 exploration으로 분류하지 않는다. 최근 명시 행동과 deterministic ontology path에서 나온 개인화 후보이며, drift confidence가 강할 때만 제한된 candidate source floor를 적용한다.

---

## 8. LightFM Feature Export 정책

### 8.1 전체 그래프와 모델 Feature를 분리

그래프에는 설명과 profile을 위해 전체 관계를 보존한다. LightFM에는 일반화 가능성과 모델 크기를 기준으로 선별한 feature만 전달한다.

```text
ontology graph
  전체 actor/director/keyword 관계

LightFM feature matrix
  빈도와 coverage 기준을 통과한 relation
```

### 8.2 최초 빈도 기준

실험 시작값:

```text
actor    movie_frequency >= 5
director movie_frequency >= 5
keyword  movie_frequency >= 5
```

추가:

- catalog 대부분에 등장하는 generic keyword는 IDF 또는 최대 빈도 기준으로 감쇠
- genre/theme/mood는 controlled vocabulary이므로 유지
- OTT는 streaming relation만 우선 ablation
- feature 제거 수와 retained movie coverage 기록

기준은 고정하지 않고 `>=2`, `>=5`, `>=10` 비교 후 결정한다.

### 8.3 Role 분리

person node는 통합하지만 LightFM feature namespace는 역할을 보존한다.

```text
actor:{people_id}
director:{people_id}
```

같은 인물이어도 actor feature와 director feature는 별도 embedding을 가질 수 있다.

---

## 9. Build Pipeline 재정의

### 9.1 새 순서

```text
1. asset schema와 relation registry 검증
2. 원천 DB fingerprint 생성
3. overview signal 추출 및 검증
4. running V3 build 생성
5. factual node/edge 생성
6. semantic evidence 생성
7. canonical has_theme/has_mood 집계
8. node/edge/evidence coverage 검증
9. LightFM feature export 사전 검증
10. build manifest와 count 저장
11. success 처리
12. 검증된 build만 원자적으로 active 전환
```

### 9.2 Build 불변성

- active build에 edge를 사후 insert/update하지 않음
- overview signal 변경 시 새 build 생성
- asset 변경 시 새 build 생성
- relation schema/aggregation 변경 시 새 build 생성
- 실패한 build가 기존 active build를 비활성화하지 않음
- LightFM artifact는 정확한 `ontology_build_id`에 고정
- V2와 V3 active build는 `schema_version` 범위별로 독립 관리
- 현재처럼 모든 active row를 한 번에 inactive로 바꾸는 `mark_build_success()`를 V3에서 그대로 호출하지 않음
- V3 serving은 model/ontology/policy 호환 조합을 bundle로 검증한 뒤 원자적으로 전환

### 9.3 Source manifest

필수:

```text
ontology_schema_version
relation_registry_version
aggregation_policy_version
engine_version
asset file hashes
overview extractor version
overview signal count/hash
actor/semantic required feature flags
DB source fingerprint
build 시작/종료 source 검증값
```

원천 테이블이 build 중 변경될 수 있으므로 시작과 종료 fingerprint가 다르면 build를 활성화하지 않는다. `source_hash`의 유일성 범위에는 `ontology_schema_version`을 포함해 V2와 V3가 같은 원천 snapshot을 각각 build할 수 있게 한다.

### 9.4 Graph Build와 Model Training 주기 분리

LightFM 재학습마다 ontology graph를 다시 만들지 않는다.

```text
ontology 입력 변경 없음
-> 기존 active V3 build와 feature artifact 재사용

사용자 행동만 변경
-> LightFM interaction/model만 재학습

영화 mapping, asset, extractor 변경
-> 새 ontology build 생성
-> feature export
-> 해당 build에 묶인 LightFM 학습
```

graph build는 asset hash, source fingerprint, extractor version과 relation schema가 변경됐을 때만 실행한다.

### 9.5 Build Catalog 제한

V2는 전체 `movies` 약 117만 개를 graph node로 만든다. V3에서는 실제로 추천 대상이 될 가능성이 없는 영화까지 모두 graph에 넣지 않는다.

`ontology_catalog` 기준:

- `adult = false`
- 서비스에서 영구적으로 노출할 수 없는 상태 제외
- 내부 movie ID와 추천에 필요한 최소 메타데이터 존재
- cold-start 신규 영화는 포함
- vote count와 popularity 같은 변동 가능한 품질값만으로 catalog에서 영구 제외하지 않음

먼저 조건별 movie 수와 held-out 정답 coverage를 계산한 뒤 catalog 기준을 확정한다. graph build 최적화를 위해 평가 대상 영화까지 제거하면 안 된다.

person, keyword 등 feature node도 catalog movie와 실제 연결된 node만 build한다.

### 9.6 Overview 증분 추출

현재 extractor의 `reset_existing=True`는 같은 extractor version의 signal을 모두 삭제한 뒤 전체 overview를 다시 검사한다.

V3:

- `movie_id`, `overview_hash`, `extractor_version` 처리 상태 저장
- overview hash가 바뀐 영화만 재추출
- signal이 0개였던 영화도 처리 상태를 남김
- asset/extractor version이 바뀌면 영향받는 규칙만 재처리할 수 있게 manifest 기록
- V3 build는 검증된 signal snapshot을 읽기만 함

권장 상태 테이블:

```text
movie_overview_extraction_state
  movie_id
  overview_hash
  extractor_version
  signal_count
  processed_at
```

### 9.7 Bulk Build 최적화

우선 적용:

- build 단계별 staging table 사용
- Python row loop 대신 `INSERT ... SELECT`와 bulk operation 유지
- 새 build의 빈 영역에는 불필요한 `ON CONFLICT` 최소화
- semantic evidence를 staging에서 먼저 중복 집계한 뒤 canonical edge insert
- batch마다 무조건 commit하지 않고 stage별 transaction과 적정 batch 크기 실험
- movie ID range가 아니라 실제 catalog ID chunk 사용
- actor/director/keyword node는 catalog mapping에서 `DISTINCT`로 직접 생성
- count를 위해 전체 table을 반복 scan하지 않도록 stage 결과 row count 기록

검토 대상:

- direct relation별 제한된 병렬 build
- `build_id` 기준 table partition
- unlogged staging table
- bulk load 후 index 생성 또는 partition attach
- 오래된 inactive build retention 정책

병렬 처리는 actor edge처럼 독립적인 range에서만 적용하고, 동일 index에 대한 write contention과 DB 서비스 영향부터 측정한다.

### 9.8 Stage별 성능 기록

필수 stage:

```text
source_fingerprint
overview_extract
movie_nodes
feature_nodes
has_genre
has_keyword
has_actor
has_director
ott_edges
asset_edges
semantic_evidence
semantic_canonicalization
validation
feature_export
activation
```

각 stage 기록:

```text
started_at
finished_at
elapsed_seconds
input_rows
output_rows
rows_per_second
peak memory 가능 시 기록
status/error
```

저장 위치:

- `ontology_builds.finished_at` 필수
- `ontology_builds.properties.stage_metrics`
- 필요하면 별도 `ontology_build_stage_runs`

현재 full build 약 60분을 기준선으로 둔다. instrumented V3 baseline에서 가장 느린 stage부터 최적화하고 다음을 완료 기준으로 둔다.

- 동일 입력이면 graph rebuild skip
- overview 미변경 영화 재추출 0
- LightFM 재학습만으로 graph rebuild 발생 0
- 모든 stage elapsed/rows 처리량 기록
- build 실패 시 기존 active graph 유지
- actor와 semantic evidence를 포함한 최초 V3 full build는 우선 60분 이내를 no-regression 목표로 설정
- 안정화 후 full build 30분 이내를 1차 최적화 목표로 검토
- 최적화 전후 build 시간과 table 크기 비교 보고

---

## 10. Relation Registry

각 relation은 다음 정보를 가진다.

```json
{
  "relation_type": "related_to",
  "source_type": "theme",
  "target_type": "theme",
  "symmetric": true,
  "active": true,
  "consumers": ["ontology_analyzer"],
  "max_hops": 1,
  "score_cap": 0.0,
  "version": "v3.0.0"
}
```

검증:

- endpoint node type
- 허용 consumer
- symmetric/inverse 일관성
- max hop
- weight/confidence/effective strength 범위
- active relation의 실제 edge 수
- 정의됐지만 consumer가 없는 relation 금지

`score_cap` 등 숫자는 정책 확정 후 config에 기록한다.

---

## 11. Migration 전략

### 11.1 V2 보존

- 활성 V2 build 3 유지
- V2 recommender는 기존 relation을 계속 사용
- V3 build 실패가 V2 serving에 영향 주지 않음

### 11.2 Schema 변경

권장 migration:

- `ontology_builds.engine_name`과 `schema_version` 추가
- `ontology_builds.source_hash` 전역 unique를 `(schema_version, source_hash)` unique로 변경
- schema별 active build partial unique index 추가
- `ontology_edges.effective_strength` nullable 추가
- `ontology_edge_evidence` 추가
- evidence 조회 index 추가
- V3 relation type은 문자열이므로 enum migration 불필요

권장 index:

```text
ontology_builds(schema_version, source_hash) UNIQUE
ontology_builds(schema_version) UNIQUE WHERE is_active = true
ontology_nodes(build_id, node_type, ref_id)
ontology_edge_evidence(build_id, edge_id)
ontology_edge_evidence(build_id, evidence_type)
ontology_edges(build_id, relation_type, source_node_id)
ontology_edges(build_id, relation_type, target_node_id)
```

현재 ORM의 node unique constraint는 요구사항을 충족하지만 조회용 composite index 존재 여부는 migration에서 별도로 확인한다. V3 activation 함수는 같은 `schema_version`의 이전 build만 inactive로 바꾸고 V2 active build는 유지해야 한다.

### 11.3 Asset 분리

```text
assets/ontology/v2/  또는 현재 위치 유지
assets/ontology/v3/
```

V3 asset에서:

- `implies_theme` -> `suggests_theme`
- `implies_mood` -> `suggests_mood`
- relation metadata와 symmetric 여부 명시
- overview noisy term 검증 결과 기록

기존 V2 asset을 직접 덮어쓰지 않는다.

---

## 12. 구현 순서

### O0. 현재 Graph Audit

완료:

- 활성 build node/edge/source 분포 확인
- actor/keyword/director cardinality 확인
- overview/asset evidence 충돌 확인
- 미사용 relation 확인

추가:

- 현재 약 60분 build를 기준으로 instrumented V3 baseline 생성
- stage별 시간과 rows/sec 측정
- catalog 조건별 movie 수와 평가 coverage 측정

### O1. V3 Relation/Asset Schema

- node/relation registry 구현
- V3 asset 디렉터리 분리
- `suggests_*` relation으로 변환
- symmetric/inverse validator 구현
- evidence source family 정의

### O2. Evidence Schema Migration

- build `engine_name/schema_version`과 schema-scoped activation
- schema-scoped source hash unique와 active partial index
- `effective_strength`
- `ontology_edge_evidence`
- ORM/CRUD/index
- V2 호환성 검증

### O3. V3 Graph Builder

- person node 통합
- actor/director role edge
- OTT 제공 방식 relation
- overview를 build 내부 단계로 이동
- evidence 생성과 canonical aggregation
- immutable activation
- graph/model build trigger 분리
- catalog 기반 node/edge build
- staging과 bulk insert
- stage metrics와 `finished_at` 기록
- unchanged source build skip

### O4. Feature Export

- 빈도 report
- actor/director/keyword pruning
- role namespace
- theme/mood/OTT sparse feature
- build/model manifest 연결

### O5. Runtime Profile/Analyzer

- role별 person preference
- evidence provenance
- controlled 1-hop
- relation별 score cap
- feature coverage report

---

## 13. 최종 판단

V2 그래프는 규모와 기본 factual relation 면에서는 재사용 가치가 충분하다. 그러나 다음 네 가지는 V3 적용 전에 반드시 수정해야 한다.

1. 활성 build를 사후 변경하지 않는 immutable build
2. semantic canonical edge와 evidence provenance 분리
3. OTT streaming/rent/buy relation 분리
4. 전체 graph와 LightFM feature cardinality 분리
5. 변경 기반 build trigger와 단계별 성능 최적화

actor와 overview는 제거할 기능이 아니라 사용 경로를 명확히 해야 할 기능이다. 반대로 relation이나 feature를 많이 추가하는 것 자체가 목표는 아니다. V3에서는 실제 consumer와 정의된 동작이 있는 관계만 active relation으로 사용한다.
