# 10. V1/V2 기준 V3 뼈대 감사

## 1. 감사 목적

V3의 후보 생성과 점수 계산보다 먼저, 실제 사용자가 추천을 소비하는 흐름이 완결됐는지 확인한다.

```text
홈 진입
-> 첫 20개 요청
-> Pin/Pass 등 행동
-> 다음 20개 요청
-> 최대 100개 소진
-> 새로고침 또는 새 세션
```

V1은 기본 동작 기준이다. V2는 V1에 없는 기능이거나 실제 연결까지 더 나은 경우만 선택한다. V1/V2에 코드가 있다는 이유만으로 완성된 기능으로 간주하지 않는다.

## 2. 우선 결론

기존 V3 점검은 후보·점수·필터 중심으로 진행됐고 피드 세션과 페이지 생명주기를 후순위 정책으로 잘못 분류했다. 따라서 기존의 `API·엔진 계약 회귀 완료` 판정은 철회한다.

현재 확인된 핵심 결함은 다음과 같다.

1. 프론트는 새로고침마다 `shuffle_seed`를 만들고 같은 페이지 묶음에 재사용한다.
2. V1은 이 값을 안정적인 순서 생성에 사용한다.
3. V3는 이 값을 추천 순서에 사용하지 않고 진단의 `feed_session_key`로만 저장한다.
4. V3는 페이지 요청마다 최대 100개를 다시 계산한 뒤 `offset/limit`으로 자른다.
5. 페이지 사이에 행동이나 상태가 바뀌면 순위가 이동해 중복 또는 누락이 생길 수 있다.
6. V3 정책에는 세션 노출 제외 입력이 있지만 실제 요청에서는 항상 빈 집합이다.
7. 기존 단위 테스트는 정적인 목록 slicing만 확인해 세션 페이지 안정성을 검증하지 않았다.

## 3. 사용자 흐름별 대조

| 사용자 단계 | V1 | V2 | 현재 V3 | 감사 판단 |
| --- | --- | --- | --- | --- |
| 첫 홈 진입 | 사전 후보와 필요 시 동적 후보를 읽음 | 요청마다 graph 후보 생성 | 장기·단기·cold 후보를 병합 | V3 후보 구조 유지 가능 |
| 새로고침 식별 | `shuffle_seed`를 실제 순서에 사용 | 내부 session key를 만들지만 API seed와 연결하지 않음 | seed를 진단에만 저장 | 뼈대 결함 |
| 첫 20개 | 전체 목록을 만든 뒤 첫 slice 반환 | 생성 결과 첫 slice 반환 | 최대 100개 정책 결과의 첫 slice 반환 | 단일 요청은 동작 |
| 같은 세션 다음 20개 | 입력 목록이 같으면 같은 seed 순서 유지 | adapter가 session key를 전달하지 않아 요청마다 새 session | 전체를 다시 계산하고 offset slice | 상태 변경 시 중복·누락 가능 |
| 노출 영화 관리 | 별도 노출 저장 없이 seed와 offset에 의존 | session schema는 있으나 저장 호출이 연결되지 않음 | policy 입력은 있으나 항상 비어 있음 | V1/V2 모두 그대로 복사 불가, `v3_new` 필요 |
| Pin/Save 후 다음 페이지 | DB 최신 상태로 목록이 바뀔 수 있음 | DB profile 재계산 | DB profile 재계산으로 순위가 움직일 수 있음 | 같은 세션 순서와 새 취향 반영 시점 분리 필요 |
| Pass/Watch 후 다음 페이지 | DB·Redis에서 제외 | DB·Redis에서 제외 | DB·Redis에서 제외 | hard filter는 즉시 유지하되 페이지 위치를 흔들지 않아야 함 |
| 후보 부족 | 요청 구간이 부족하면 동적 후보 보충 | graph 부족분을 fallback으로 보충 | top-150 중 hard filter 통과 순서로 최대 100개 | 사용자 결정에 따른 100+50 구조 유지 |
| 100개 소진 | 큰 pool과 동적 후보를 계속 사용 가능 | candidate limit 내 계속 생성 | 현재 100개 이후 빈 결과 | 새 세션·새로고침 정책과 연결 필요 |
| 새로고침 | 새 seed로 다른 안정적 순서 | API seed 의미 없음 | 같은 후보와 순서가 다시 나올 수 있음 | 새 세션 생성 의미 미구현 |

## 4. V1에서 우선 보존할 뼈대

- 기존 HTTP 경로와 응답 변환
- 페이지 시작 위치와 크기 계약
- 같은 새로고침 묶음을 식별하는 입력
- watched/passed의 DB·Redis 즉시 제외
- 후보 부족 시 후순위 후보를 검사하는 구조
- worker 중복 실행 방지, retry, 불완전 결과 게시 거부, 이전 결과 유지
- V3 실패 시 화면 fallback을 유지하되 실패를 관측하는 구조

V1의 전체 무작위 shuffle과 500개 pool을 그대로 복사하지 않는다. V3는 정확도 순위를 유지해야 하고 후보 상한은 사용자 결정대로 100개다.

## 5. V2에서 선택적으로 가져올 부분

V2의 `SessionProfile`은 다음 필드를 정의한다.

- 세션 키
- 노출 영화
- 넘긴 영화
- 체류 시간
- 세션 positive/negative concept

하지만 현재 V2 adapter는 API의 `shuffle_seed`를 전달하지 않고, `save_session_profile` 호출도 실제 추천·행동 흐름에 연결돼 있지 않다. 따라서 V2 세션 코드를 완성품으로 가져오지 않고 데이터 계약만 참고한다.

V2의 나머지 참조 범위도 다음처럼 제한한다.

| V2 요소 | V3 선택 | 이유 |
| --- | --- | --- |
| version이 있는 ontology node·edge·evidence | 채택 후 V3 schema 경계로 보완 | V1에 없는 의미 그래프와 근거 보존 구조 |
| actor/director/genre/keyword 관계와 overview 기반 theme·mood | 채택 | LightFM feature, profile, 단기·cold 후보, 설명 근거에 공동 사용 |
| 후보 집합 단위 bounded graph query | 채택 | 후보별 query와 전체 graph scan을 피함 |
| graph build·asset 검증 패턴 | 채택 후 immutable build/bundle로 보완 | V1에 동등한 graph 수명주기가 없음 |
| graph-only 후보 생성과 V2 최종 scorer/ranker | 미채택 | V3 장기 후보는 LightFM이 담당하고 ontology·정책 효과를 분리해야 함 |
| V2 점수 상수 | 미채택 | 같은 평가 없이 최신 버전이라는 이유로 가져오지 않음 |
| `SessionProfile` | schema 아이디어만 참고 | API seed, 행동 저장, 실제 추천 호출에 연결되지 않음 |
| 전체 후보 진단 집계 | 미채택 | 상위 bounded 후보에만 상세 근거를 조회하는 V3 구조 사용 |

따라서 “온톨로지는 V2, 세밀한 추천 동작은 V1 우선”이라는 원칙은 유지된다. 단, V1에 없거나 V3 구조에서 새로 필요한 LightFM, bundle, 단기 후보, 세션 저장, durable 행동 전달은 `v3_new`로 명시한다.

## 6. 정책 출처 registry 감사

현재 registry는 다음 비교를 기록한다.

- 직접 행동과 학습 가중치
- passed 처리
- 최근성
- watched/passed와 OTT filter
- ontology 조회와 semantic negative
- 점수 구성, 품질, 반복 감점
- cold-start와 후보 생성
- serving bundle 활성화와 rollback

하지만 다음 뼈대 결정은 registry에 없다.

| 빠진 결정 | V1 기준 | V2 참고 가능 범위 | 필요한 분류 |
| --- | --- | --- | --- |
| 같은 피드 세션 식별 | `shuffle_seed` | session key schema | `v3_new` |
| 연속 페이지 순서 고정 | 같은 seed의 안정적 순서 | 노출 상태 schema | `v3_new` |
| 새로고침 의미 | 새 seed | refresh count schema | `v3_new` |
| 행동 후 현재 세션 처리 | 최신 DB·blacklist | session profile 입력 | `v3_new` |
| 페이지 노출 기록 | 없음 | 필드는 있으나 저장 미연결 | `v3_new` |
| worker 중복 실행·retry | advisory lock, 사용자별 retry, 기존 결과 유지 | 직접 대응 없음 | `v1` 우선 |
| 행동 전달 실패 복구 | Redis best-effort까지만 있음 | 직접 대응 없음 | `v3_new` durable 전달 |
| 화면 fallback 관측 | fallback 자체 | request run 진단 | `v3_new` |

P1-08 정책 출처 감사는 완료된 후속 문서 작업이 아니다. 위 결정이 registry와 설계 문서에 들어가기 전에는 V1 참고가 완료됐다고 판단하지 않는다.

## 7. 기능 영역별 1차 감사

| 기능 영역 | 현재 판단 | 다음 문서 작업 |
| --- | --- | --- |
| 후보 생성 | V3 구조 유지 가능 | 100+50과 세션 진행 관계 명시 |
| 협업 신호 | V1 수기 유사도 대신 LightFM 사용 타당 | 실제 사용자 학습 전 품질 판단 금지 |
| cold-start | V1 fallback을 보존하며 V3 의미 후보 보강 | 세션과 무관한 품질 작업은 후순위 유지 |
| 제외·OTT | V1 기본 의미 반영 | 페이지 중간 변경 시 위치 처리 명시 |
| 단기 취향 | 독립 후보 생성 구조 반영 | 현재 세션/다음 세션 적용 시점 명시 |
| 피드 세션 | 미구현 | 최우선 설계 대상 |
| 페이지네이션 | 단일 요청 slicing만 구현 | 세션 연속 페이지 계약 필요 |
| 새로고침 | API 입력만 존재 | 새 세션 생성으로 정의 필요 |
| 행동 전달 | Redis best-effort | durable 전달·복구 계약 필요 |
| 정기 학습·게시 | 수동 job만 존재 | V1 worker 안전성 기준으로 scheduler 감사 |
| 장애 fallback | 화면 결과는 유지 | V3 실패가 숨지 않게 관측 계약 필요 |

### 7.1 V1 worker 안전성 대조

| 안전장치 | V1 | 현재 V3 | 판단 |
| --- | --- | --- | --- |
| 중복 실행 방지 | PostgreSQL advisory lock | candidate snapshot ID별 파일 lock, short-term processing lease | 개별 stage는 존재하나 전체 pipeline lock 필요 |
| 일시 실패 재시도 | 사용자별 최대 3회와 1/2/4초 backoff | short-term worker 재예약, offline 전체 stage 공통 retry 없음 | `v1` 기준 보완 필요 |
| 사용자 실패 격리 | 실패 사용자는 이전 추천 유지 | candidate block 실패를 사용자 단위로 격리 | 반영됨 |
| 후보 검증 | 최소 수, 중복, finite score, DB 존재 확인 | snapshot hash/rank/중복/finite와 게시 전 사용자·exclusion 검증 | V3 검증이 더 강함 |
| 원자 게시 | 사용자별 교체 transaction | snapshot 게시 transaction과 immutable bundle pointer | 반영됨 |
| 실패 시 정상 결과 유지 | 검증 실패 사용자는 기존 추천 유지 | failed user 보존, 잘못된 bundle은 직전 bundle 유지 | 반영됨 |
| 정기 실행 | 하루 1회 V1 worker | V3 end-to-end scheduler 없음 | 뼈대 결함 |

현재 `app/jobs/recsys/scheduler.py`는 `RECOMMENDATION_ENGINE` 값을 보고 engine을 선택하지 않고 V1 worker를 직접 import한다. V3의 candidate job이 존재한다는 사실과 V3 전체 파이프라인이 정기 실행된다는 것은 다른 의미다.

V3 정기 실행은 최소한 다음 단계를 하나의 게시 계약으로 묶어야 한다.

```text
DB 행동 snapshot
-> dataset/feature compatibility 검사
-> LightFM 학습
-> top-150 candidate snapshot
-> DB 후보 게시 검증
-> model/ontology/candidate/policy bundle 검증
-> active pointer 교체
```

어느 단계든 실패하면 불완전한 조합을 활성화하지 않고 이전 정상 bundle을 유지해야 한다.

### 7.2 행동 저장과 갱신 전달 대조

현재 행동 API는 DB 저장 성공 후 Redis에 profile version, blacklist, 단기 positive 누적 또는 갱신 예약을 best-effort로 기록한다.

```text
DB 저장 성공
-> Redis 기록 성공: worker가 단기 후보를 선계산
-> Redis 기록 실패: DB 상태는 남지만 선계산 예약은 유실 가능
```

다음 추천 요청은 DB에서 최신 profile과 watched/passed를 읽으므로 필터와 현재 후보 평가는 복구된다. 그러나 “새로운 단기 후보를 미리 생성해야 한다”는 작업 유실은 DB fallback만으로 복구되지 않는다. 이 문제는 V1의 best-effort 방식을 그대로 유지해서는 해결되지 않으며 durable outbox 또는 DB pending marker가 필요한 `v3_new` 뼈대다.

### 7.3 실제 화면 API 대조

| 화면 | 현재 호출 | 세션 관점 문제 |
| --- | --- | --- |
| 홈 숏츠 | 한 페이지 20개, 새로고침마다 seed 생성, 다음 페이지에도 같은 seed 전달 | V3가 seed를 순서·세션에 사용하지 않음 |
| 홈 Pass | 프론트에서 즉시 제거하고 부족하면 다음 page 요청 | 서버 page 순위가 다시 계산되면 중복·누락 가능 |
| 탐색 추천 호환 route | 서버 route는 있으나 현재 프론트 직접 호출은 확인되지 않음 | engine별 max page 크기와 seed 부재를 계약으로 재검토해야 함 |

홈 `/shorts` 응답은 내부 `RecommendationResponse`의 `has_more`를 프론트에 전달하지 않는다. 프론트는 빈 페이지가 나올 때까지 요청하므로, 100개 소진과 새 세션 생성 의미를 서버·프론트가 함께 이해하도록 문서 계약이 필요하다.

### 7.4 V1 기능 전수 대장

이 표는 V1의 코드를 그대로 복사할지를 판단하는 표가 아니다. V1이 담당하던 사용자 동작과 운영 책임이 V3에서 어디로 이동했는지 확인하는 대장이다.

| V1 기능·정책 | 현재 V3 판단 | 분류 | 후속 |
| --- | --- | --- | --- |
| pinned, watched, passed 행동 점수 | V3 dataset과 runtime profile에 연결됨. saved는 playlist 저장 시각으로 별도 표현 | `v3_new` 대체·확장 | 가중치 품질 조정은 후순위 |
| watched/passed 추천 제외 | DB와 Redis blacklist, materialization 제외, 요청 hard filter에 연결 | `v1` 의미 보존, V3 다중 방어 | 세션 중 탈락 자리 처리만 미결정 |
| 행동 최근성 감쇠 | 장기 학습 snapshot과 runtime profile에 존재 | `v1` 의미 보존 | 반영 시점 계약 P0-05 필요 |
| genre, keyword, director, actor 취향 | ontology feature/profile/analyzer에 모두 존재 | `v2` graph 방식으로 확장 | theme·mood까지 포함해 구현됨 |
| 사용자 유사도 협업 후보 | 수기 cosine 대신 hybrid LightFM으로 대체 | `v3_new` | 실제 사용자 품질 검증은 후순위 |
| content 60%, collaborative 20%, explore 20% 고정 quota | 장기 model, 독립 단기 후보, cold source 병합으로 대체 | `v3_new` | V1 비율을 그대로 이식하지 않음 |
| 인기작 exploration 20% | 사용자가 정확도 기준선 전에는 구현하지 않기로 결정 | 명시적 보류 | `08` P4에서만 재검토 |
| playlist 저장, 게시글, 좋아요, 댓글 신호 | playlist 저장은 반영. social은 provenance 투영만 하고 학습 eligibility는 false | 일부 미구현 | `08` P1-01에서 방향 결정 |
| favorite 영화와 선호 장르 cold-start | feature-only LightFM과 ontology 규칙 후보로 확장 | `v1` 의미 + `v3_new` | 현재 세션 결함과 별개로 동작 |
| cold-start 인기 fallback | 의미 후보가 없을 때만 보수적 품질 fallback 사용 | `v1` fallback을 축소 보존 | 품질 고도화 전 구조 유지 |
| OTT 구독 보너스와 subscribed-only filter | OTT를 model feature에서 제외하고 최신 DB filter·정책 입력으로 사용 | V3 방식이 더 명확 | 구현됨 |
| 사전 후보 부족 시 요청 시 동적 보충 | top-150의 예비 50개로 hard filter 탈락만 보충 | 일부 대체 | 100개 소진·새 세션은 미결정 |
| 같은 seed의 안정적 shuffle과 offset page | seed가 diagnostics에만 들어가고 순서에 미사용 | 미구현 | 최우선 F0~F3 |
| `count`, `has_more`, `source` 응답 | 엔진 응답 schema는 유지. 홈 `/shorts`는 `has_more`를 버림 | 일부 연결 | F4 API·프론트 계약 필요 |
| 사용자별 후보 중복·finite·DB 존재 검증 | snapshot hash/rank/중복/finite와 게시 전 검증으로 강화 | V3가 더 강함 | 유지 |
| worker 중복 실행 방지와 retry | 개별 stage lock/lease/checkpoint는 있으나 전체 pipeline lock/retry 없음 | 일부 연결 | production scheduler와 함께 보완 |
| 사용자 실패 시 기존 결과 유지 | failed user 보존과 invalid bundle 이전 버전 유지 | 반영됨 | end-to-end 장애 회귀 필요 |
| 행동 DB 저장 뒤 Redis cache 갱신 | best-effort 구조를 확장했지만 선계산 예약 유실 가능 | 뼈대 결함 | P0-11 durable 전달 필요 |
| API fallback으로 화면 결과 유지 | fallback은 남아 있으나 V3 실패가 성공 응답 뒤에 숨을 수 있음 | 관측 미구현 | P0-09 |

### 7.5 뼈대와 후순위 구분

다음 항목은 추천 점수를 좋게 만드는 고도화가 아니라, 서비스가 요청 간에 일관되게 동작하기 위한 뼈대다.

1. 추천 세션과 연속 페이지
2. 행동 반영 시점과 갱신 작업의 유실 복구
3. V3 전체 학습·후보·bundle 게시 scheduler와 실패 시 이전 결과 유지
4. API fallback 뒤의 V3 장애 관측
5. 기존 응답 계약과 실제 홈 화면 계약의 일치

반면 social 방향·가중치, 유사 사용자 품질, LightFM tuning, 탐색 후보, ontology 점수 조정, 응답 시간 단축은 위 뼈대가 아니다. 해당 항목을 먼저 구현해 구조 결함을 가리지 않는다.

## 8. V3에서 새로 확정해야 할 뼈대

아래 F0~F4는 현재 동작 설명이 아니라 감사를 바탕으로 만든 **작업 제안**이다. 사용자 결정과 API·프론트 계약 확인 전에는 확정 정책이나 구현 완료로 기록하지 않는다.

### F0. 추천 세션

- 사용자 ID와 프론트의 `shuffle_seed`를 결합해 세션을 식별한다.
- 첫 요청에서 순위 후보와 페이지 진행 상태를 저장한다.
- 다른 사용자가 같은 seed를 보내도 세션이 섞이지 않아야 한다.
- TTL과 bundle·policy 호환성을 검증한다.

### F1. 안정적인 페이지 진행

- 같은 세션의 page 1, 2, 3은 같은 순위 기준을 사용한다.
- 이미 반환한 영화는 다시 반환하지 않는다.
- 반복된 동일 페이지 요청의 의미를 정한다.
- 최신 hard filter로 탈락한 영화는 예비 후보로 보충하되 다음 페이지와 중복되지 않아야 한다.

### F2. 행동 반영 경계

- Pass/Watch와 서비스 불가 상태는 현재 세션에도 즉시 제외한다.
- Pin/Save의 취향 변화는 DB와 단기 누적에는 즉시 기록한다.
- Positive 행동으로 현재 세션의 남은 순서를 움직일지, 다음 세션부터 적용할지 명시한다.
- 단기 worker가 만든 새 후보가 현재 세션과 다음 세션 중 어디부터 적용되는지 명시한다.

### F3. 새로고침과 100개 소진

- 새로고침은 새 추천 세션 생성으로 정의한다.
- 새 세션에서는 최신 profile, 제외 상태, 단기 후보로 top-150을 다시 구성한다.
- hard filter 후 최대 100개만 노출한다.
- 전체 무작위 shuffle을 정확도 정책으로 오해하지 않는다.

### F4. 응답과 실패 계약

- `offset`, `limit`, `count`, `has_more`, `source` 의미를 세션 기준으로 검증한다.
- 홈 `/shorts`와 탐색 추천 route가 엔진별로 다른 페이지 의미를 갖지 않게 한다.
- fallback 성공이 V3 실패를 숨기지 않도록 별도 진단을 남긴다.

## 9. 현재 테스트의 잘못된 완료 판단

`test_recommender_preserves_response_pagination_contract`는 한 번 만들어진 정적 후보 네 개에서 `offset=1`, `limit=2`가 두 개를 반환하는지만 확인한다. 다음 항목은 검증하지 않는다.

- 같은 seed의 연속 페이지
- 다른 seed의 새로고침
- page 1과 page 2 사이의 Pin/Save
- page 1과 page 2 사이의 Pass/Watch
- hard filter 보충 후 페이지 중복
- 100개 소진과 `has_more`
- Redis 세션 손상·만료·재시도

따라서 기존 pagination 테스트 통과를 피드 페이지네이션 완료 근거로 사용하지 않는다.

## 10. 뼈대 감사 후 구현 순서

```text
1. 추천 세션 의미와 행동 반영 경계 확정
2. session 저장 계약과 안정적 페이지 진행 구현
3. hard filter와 예비 후보 보충을 session 진행에 연결
4. 새로고침·100개 소진 의미 연결
5. 행동 전달 보장과 단기 worker SLA 연결
6. LightFM 학습·후보·bundle scheduler 연결
7. 위 흐름의 API·장애·rollback 회귀 검증
8. 그 뒤에 정책 미구현과 추천 품질 고도화 진행
```

## 11. 감사 상태

- 피드 세션·페이지네이션: 결함 확인
- `shuffle_seed`: API 의미 미연결 확인
- 세션 노출 입력: 미연결 확인
- 행동 후 페이지 안정성: 테스트 부재 확인
- V1 worker 안전성 대비 V3 개별 job: 반영 범위 확인
- V3 end-to-end 정기 scheduler: 미구현 확인
- API 두 추천 화면의 page/refresh 의미: 홈 결함 확인, 탐색 호환 route 후속 결정 필요
- 행동 commit과 Redis/worker 전달 보장: best-effort 유실 가능성 확인, `08` P0-11과 통합

## 12. 확정 사실과 미결정 사항

### 코드와 화면에서 확인된 사실

- 프론트는 같은 페이지 묶음에 같은 `shuffle_seed`를 보낸다.
- V3는 해당 seed를 실제 후보 순서 고정에 사용하지 않는다.
- V3의 세션 노출 제외 입력은 현재 요청에서 비어 있다.
- 현재 페이지 처리는 요청마다 다시 만든 결과에 `offset/limit`을 적용한다.
- 기존 테스트는 상태가 고정된 한 요청 결과의 slicing만 증명한다.
- V3 전체 학습·후보·bundle 게시를 정기 실행하는 production scheduler는 없다.
- DB commit 뒤 Redis 알림 실패 시 단기 후보 선계산 작업이 유실될 수 있다.

### 구현 전에 결정할 서비스 의미

- 같은 세션에서는 positive 행동 후에도 남은 순위를 고정할지
- Pass/Watch로 빠진 자리를 어느 예비 후보가 채우며 페이지 위치를 어떻게 유지할지
- 동일 페이지 재요청을 같은 결과로 재전송할지, 아직 미노출인 다음 결과로 취급할지
- 세션 상태의 TTL과 bundle 변경 시 폐기 기준을 무엇으로 할지
- 100개 소진 후 빈 응답을 유지할지, 새 seed/명시적 새로고침만 새 후보를 만들게 할지
- 내부 `has_more`를 홈 화면 계약에 어떻게 전달할지

### 참조 원칙

V1도 상태가 페이지 사이에 바뀌면 seed만으로 모든 연속성을 보장하지 못하고, V2도 session schema가 실제 adapter와 행동 저장에 연결되지 않았다. 따라서 세션 뼈대는 V1 또는 V2를 그대로 복사하는 항목이 아니라, V1의 사용자 계약과 안전장치 및 V2의 데이터 표현을 근거로 새로 확정할 `v3_new` 항목이다.
