# 07. V3 전체 추천 흐름

## 목적

사용자 행동이 어디에 저장되고 장기·단기 후보와 최종 추천에 언제 반영되는지 설명한다. 시스템 구조와 후속 작업은 각각 [01 아키텍처와 파이프라인](01_architecture_and_pipeline.md), [08 후속 작업](08_additional_work_backlog.md)을 따른다.

## 용어

| 용어 | 의미 |
| --- | --- |
| 학습·사전 계산 | 사용자 요청 전에 dataset, model, 장기 후보를 만드는 작업 |
| 요청 시 계산 | 사용자가 추천을 요청한 순간 profile, 후보 병합, filter와 순위를 계산하는 작업 |
| LightFM 장기 후보 | 현재 활성 LightFM model로 사용자별 미리 저장한 top-150 |
| 장기 ontology 후보 | 요청 시 장기 profile의 의미 feature로 독립 조회한 최대 100개 |
| 단기 후보 | 최근 positive 행동에서 ontology 관계로 독립 생성한 후보 |
| 활성 후보 | top-150의 앞 100개 |
| 예비 후보 | hard filter 탈락분만 보충하는 뒤 50개 |
| 상세 분석 대상 | hard filter를 통과해 ontology/policy가 처리하는 최대 100개 |
| 단기 worker | 최근 행동을 모아 단기 ontology 후보를 갱신하는 작업. LightFM은 학습하지 않음 |
| 장기 재학습 | 새 행동을 dataset에 포함해 LightFM model, 장기 후보와 bundle을 다시 만드는 작업 |
| drift | 최근 positive의 의미 방향이 비교 가능한 장기 취향과 충분히 멀어진 상태 |
| cold-start | 활성 model에 user identity가 없거나 장기 행동이 부족한 상태 |

`offline`, `online`이라는 표현만으로 구분하지 않고 문서에서는 `학습·사전 계산`과 `요청 시 계산`을 사용한다.

## 전체 구조

```text
[학습·사전 계산]
DB 행동 snapshot
  -> ontology item/user feature
  -> hybrid LightFM 학습
  -> 사용자별 장기 top-150
  -> model + graph + candidate + policy bundle 활성화

[최근 행동 갱신]
DB positive 행동
  -> Redis 누적·예약
  -> 단기 worker
  -> 단기 ontology 후보 cache

[추천 요청]
DB 최신 profile·제외·OTT
  + 장기 top-150 또는 cold 후보
  + 단기 ontology 후보
  -> source 정규화·병합
  -> hard filter와 예비 보충
  -> 최대 100개 ontology 상세 분석
  -> policy, drift lane, 반복 감점, MMR
  -> offset/limit 응답
```

## 학습·사전 계산

### Ontology graph

영화의 장르, keyword, 배우, 감독, overview 기반 theme/mood와 evidence를 immutable build로 생성한다. 현재 기준은 build `22`다.

Graph는 다음 용도로 쓰인다.

- LightFM item/user feature
- 장기·단기 runtime profile
- 단기와 cold-start 후보 생성
- 후보의 semantic score와 근거
- negative 취향과 반복 유사도

영화 metadata가 바뀌어도 현재 graph가 자동 갱신되지는 않는다. 자동 반영은 `08` P0-05다.

### LightFM 학습

현재 직접 positive 신호는 saved, pinned, watched, favorite이며 passed는 positive로 학습하지 않는다. 같은 user/movie의 신호는 한 행으로 합치고 passed 충돌 시 passed가 이긴다.

Ontology feature는 모델 입력이지만, 추천 근거가 LightFM 점수의 인과 설명은 아니다. 모델 점수, ontology 점수와 정책 효과를 분리 기록한다.

게시글·좋아요·댓글은 projector와 진단만 있으며 현재 LightFM 학습에는 들어가지 않는다.

### 장기 후보

전체 user-by-movie dense matrix를 만들지 않고 blockwise exact top-K를 계산한다. 사용자별 150개를 저장하며 앞 100개는 활성, 뒤 50개는 hard filter 예비 후보다.

장기 후보는 model artifact가 바뀌지 않는 한 단기 worker를 반복 실행해도 달라지지 않는다.

장기 ontology 후보는 현재 DB 행동으로 만든 장기 profile을 사용하므로 새 행동이 다음 요청부터 반영된다. 이 점수는 LightFM 점수와 별도 source로 기록하며 LightFM의 인과 설명으로 사용하지 않는다.

## 사용자 profile

요청 시 DB 현재 상태로 positive와 negative 행동을 다시 읽는다.

### 장기 ontology profile

현재 positive 행동 전체를 장기 감쇠 규칙으로 집계한다. 새 행동은 다음 요청부터 이 profile에 포함된다. 따라서 단기 행동이 잠깐 사용된 뒤 버려지는 구조가 아니다.

다만 LightFM 장기 후보는 별개다. 새 행동이 다음 dataset snapshot, 재학습과 candidate 재생성에 포함돼야 model 기반 장기 후보가 바뀐다.

### 단기 profile

최근 30일의 최대 50개 행동을 더 강한 시간 감쇠로 집계한다. 단기 profile은 최근 방향을 추가 강조하고 독립 후보를 만드는 경로다.

상태 판정:

- `inactive`: 최근 근거 부족
- `recent_interest`: 최근 근거는 충분하지만 장기 비교 근거 부족
- `stable`: 장기와 최근 의미 방향이 가까움
- `drift`: 장기와 최근 의미 거리가 0.70 이상

### Negative profile

passed와 최근 negative는 exact movie exclusion과 semantic negative 감점으로 나눠 적용한다. exact passed/watched는 항상 hard filter가 우선한다.

## 단기 후보 갱신

Positive 행동을 하나 할 때마다 무조건 후보를 다시 계산하지 않는다.

현재 갱신 기준:

- 최근 24시간 서로 다른 positive 영화 3편
- 또는 서로 다른 영화 2편이면서 행동 weight 합 2.0 이상
- threshold 도달 후 30초 debounce
- 최초 예약 후 최대 2분 안에는 실행
- scheduled 작업 lease 15분

Passed와 OTT 변경은 단기 후보를 다시 만들지 않는다. 최신 exclusion과 OTT policy가 다음 요청에서 즉시 적용되기 때문이다.

Cache는 build/user/format signature를 포함한 format 3이며 저장 후 6시간과 사용자별 최대 30분 jitter를 사용한다. cache miss에서는 DB fallback이 유지된다.

## 알려진 사용자 요청

```text
1. 활성 bundle 확인과 model memory cache 조회
2. DB에서 현재 행동·온보딩·OTT·제외 정보 조회
3. 장기·단기 runtime profile 생성
4. 저장된 LightFM top-150 조회
5. 장기 profile 기반 ontology 후보 최대 100개 조회
6. 단기 candidate cache 조회, 필요 시 bounded DB fallback
7. source별 percentile 정규화와 후보 병합
8. hard filter 적용, 탈락 수만큼 예비 50개 검사
9. 최대 100개 ontology 상세 분석
10. personal/ontology 점수와 정책 효과 계산
11. drift인 경우 short-only lane 적용
12. 반복 감점과 결정적 MMR
13. 전체 순서에서 offset/limit slice 반환
```

Personal/ontology 기본 비율은 `0.75/0.25`다. Quality, negative, OTT와 반복 정책은 bounded adjustment로 적용하며 상세 숫자는 [04 LightFM 조정 지점](04_lightfm_tuning.md)에 있다.

## Cold-start 요청

Model identity가 없는 사용자는 온보딩 장르와 선호 영화로 feature-only LightFM 점수를 계산하고 ontology 규칙 후보와 결합한다.

- 정상 온보딩: ontology 70%, feature-only model 30%
- 장르만 남은 방어 경로: ontology 85%, model 15%
- overview evidence가 있는 경우에만 장르에서 theme/mood로 확장
- model mapping에 없는 graph 영화는 `ontology_cold_item` source로 구분
- 의미 후보가 없을 때만 품질 fallback
- OTT는 후보 feature가 아니라 최종 filter/context

온보딩 변경 known user는 다음 요청에서 변경을 감지해 feature-only top-150을 계산·저장한다. 정기 장기 재학습 전까지 identity model 자체가 바뀌는 것은 아니다.

## 최종 정책 순서

1. DB 존재, 상태, adult/title 조건
2. watched, passed, blacklist와 요청에 전달된 session exclusion. 현재 session 입력 연결은 미구현이다.
3. OTT mode filter
4. personal score와 ontology score 결합
5. catalog 신뢰도, recency, OTT bonus와 semantic negative
6. drift short-only lane
7. genre/actor/director/theme/mood 반복 감점
8. 결정적 MMR과 최종 tie-break

Vote count 20 미만은 최대 0.05 soft 감점을 받지만 일반 후보의 최소 투표 수 hard filter는 없다. 장르-only cold 방어 경로의 최소 1표 조건은 별도다.

## 사용자 행동별 반영

| 행동 | 다음 요청 | 단기 worker 후 | 장기 후보 재생성·LightFM 재학습 후 |
| --- | --- | --- | --- |
| pin/save/watch | profile·filter에 즉시 반영 | 단기 후보 확장 | 장기 model 후보 반영 |
| pin/save/watch 삭제 | profile·filter에 즉시 반영 | cache 갱신 시 제거 | 장기 model 후보 반영 |
| pass | 즉시 hard exclusion·negative 반영 | 후보 재생성 안 함 | positive 학습에서 제외 |
| pass 해제 | exclusion 즉시 해제 | 후보 재생성 안 함 | 같은 model의 장기 후보 재생성만으로도 완전 복구 가능 |
| favorite 변경 | profile·cold feature에 반영 | 해당 없음 | 장기 user feature 반영 |
| 선호 장르·영화 변경 | feature-only 후보 재계산 | 해당 없음 | 장기 user feature 반영 |
| OTT 변경 | filter/context 즉시 반영 | 재생성 불필요 | model에는 미사용 |
| 게시글·좋아요·댓글 | 현재 추천에 미반영 | 미반영 | eligibility 결정 전까지 미반영 |

### 취향 변화 예시

범죄 영화를 보던 사용자가 로맨스 영화를 연속 pin/save하면 다음과 같이 움직인다.

1. DB 행동이 다음 요청의 장기 ontology profile과 단기 profile 모두에 포함된다.
2. threshold를 넘으면 단기 worker가 로맨스 관련 독립 후보를 만든다.
3. 의미 거리가 충분하면 `drift`로 판정한다.
4. 최종 100개에 confidence에 따라 15~40% short-only lane을 적용한다.
5. 이후 전체 LightFM 재학습에서 새 행동이 장기 model 후보에도 반영된다.
6. 시간이 지나 단기 강조가 줄어도 행동 자체는 DB와 장기 profile에서 사라지지 않는다.

단기 worker를 여러 번 실행하는 것과 LightFM을 여러 번 재학습하는 것은 다르다. 전자는 단기 후보 갱신이고, 후자만 model 기반 장기 취향을 바꾼다.

## 반영 시점 요약

| 정보 | 즉시 다음 요청 | 단기 worker | LightFM 재학습 | graph 재생성 |
| --- | ---: | ---: | ---: | ---: |
| 행동 현재 상태 | 예 | positive면 후보 확장 | 장기 후보 | 불필요 |
| 행동 삭제·해제 | 예 | cache 갱신 | 장기 후보 | 불필요 |
| 온보딩 장르·영화 | 예 | 불필요 | 장기 feature | 불필요 |
| 구독 OTT | 예 | 불필요 | 불필요 | 불필요 |
| 소셜 행동 | 현재 아니오 | 아니오 | 현재 아니오 | 불필요 |
| 영화 품질·개봉일·상태 | 예 | 불필요 | 후보 필요 시 | 의미 관계가 아니면 불필요 |
| 장르·배우·keyword·overview | 기존 graph 기준 | 기존 graph 기준 | 새 graph 기반 model | 예 |

## 현재 미완료 경계

다음은 흐름 설명에 필요한 사실만 적고 세부 계획은 [08 후속 작업](08_additional_work_backlog.md)에 둔다.

- V3 전체 재학습·candidate·bundle production scheduler 없음
- DB commit 후 Redis 단기 작업 예약의 durable 전달 없음
- 같은 feed session의 순서, 노출 기록, `shuffle_seed`, 새로고침 의미 미연결
- 영화 metadata 변경의 graph/model/bundle 자동 반영 없음
- 소셜 행동 training eligibility 미연결
- 상세 evidence path 없음
- 실제 사용자 규모의 협업 품질 미검증

## 주요 코드

| 기능 | 위치 |
| --- | --- |
| 전체 요청 | `app/services/recsys/v3/recommender.py` |
| runtime profile | `app/services/recsys/v3/profiles/profile_builder.py` |
| LightFM retrieval | `app/services/recsys/v3/retrieval/lightfm_retriever.py` |
| 장기 ontology retrieval | `app/services/recsys/v3/retrieval/long_term_ontology_retriever.py` |
| 후보 병합 | `app/services/recsys/v3/retrieval/candidate_merger.py` |
| 단기 갱신 기준 | `app/services/recsys/v3/retrieval/short_term_refresh_policy.py` |
| 단기 worker | `app/jobs/recsys/v3/workers/short_term_candidate_worker.py` |
| ontology 분석 | `app/services/recsys/v3/retrieval/ontology_analyzer.py` |
| 최종 정책 | `app/services/recsys/v3/policy/policy_engine.py` |
| LightFM 학습 | `app/jobs/recsys/v3/training/train_hybrid_model.py` |
| 장기 후보 | `app/jobs/recsys/v3/candidates/candidate_materializer.py` |
| bundle 게시 | `app/jobs/recsys/v3/serving/serving_bundle_publisher.py` |
| graph build | `app/jobs/recsys/v3/ontology/ontology_build_pipeline.py` |
