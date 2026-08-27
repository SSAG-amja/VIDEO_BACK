# 추천 엔진 공통 오프라인 평가

이 모듈은 V1, V2, V3 이후의 추천 엔진을 **동일한 입력, 동일한 후보, 동일한 지표와 동일한 출력 형식**으로 비교한다. 실행기는 엔진 버전에 따른 조건문이나 점수 공식을 갖지 않는다. 선택된 엔진이 공통 `prepare → rank → close` 계약을 구현하면 같은 명령으로 평가할 수 있다.

## 한 줄 실행

```powershell
docker compose exec -T back-api python -m evaluation "테스트-이름" --engine v1 --dataset fixed-v1
docker compose exec -T back-api python -m evaluation "테스트-이름" --engine v2 --dataset fixed-v1
```

- `테스트-이름`: 결과 디렉터리 이름이다. 알고리즘이나 실험 조건이 드러나게 작성한다.
- `--engine`: `v1`, `v2`, 이후 `v3`, `v4` 등을 명령어에서 직접 선택한다.
- `--dataset`: 고정 평가 데이터 버전이다. 기본값은 `fixed-v1`이다.
- `--engine`을 생략하면 `.env`의 `RECOMMENDATION_ENGINE`을 사용한다.
- `.env`를 수정하거나 컨테이너를 다시 만들 필요 없이 `--engine`으로 버전을 바꿀 수 있다.
- V3는 현재 스켈레톤이므로 `V3NotReadyError`가 발생하는 것이 정상이다.

한 번 실행하면 `cohorts.json`에 고정된 10/50/100/150/200/500명 cohort를 모두 순서대로 평가한다. 사용자 계산은 서로 독립된 DB 세션에서 최대 4개까지 병렬로 수행하며, 각 cohort의 완료 수, 경과 시간과 예상 잔여 시간을 출력한다.

## 평가 흐름

```text
fixed dataset
  └─ 사용자별 시간순 train 70% + holdout 30%
        ↓
선택 엔진 prepare(train)
        ↓
선택 엔진 rank(user, 동일 holdout 영화 ID)
        ↓
공통 NDCG / Recall / coverage 계산
        ↓
JSON 상세 결과 + CSV 요약 저장
```

평가 입력에는 holdout 평점이 들어가지 않는다. 엔진에는 과거 70% 기록과 후보 영화 ID만 전달하고, 실제 평점은 `benchmark.py`가 순위 결과를 채점할 때만 사용한다. 따라서 정답 누수가 없다.

## 왜 이 방식을 선택했는가

### 선택한 기준: 고정 후보 holdout ranking

사용자가 실제로 평가한 영화만 선호 정답을 가지고 있다. 보지 않은 영화는 사용자에게 잘 맞더라도 평점이 없으므로 좋아하는지 싫어하는지 단정할 수 없다. 이 평가에서는 최근 30%의 평가 영화를 숨긴 뒤, 각 엔진이 그 영화들을 얼마나 선호도순으로 잘 정렬하는지 측정한다.

이 기준의 목적은 다음과 같다.

- 엔진 버전마다 동일한 사용자와 동일한 후보를 제공한다.
- 후보 탐색량 차이가 아니라 개인화 점수와 순위 품질을 비교한다.
- V1/V2 회귀 점수를 재현하고 이후 변경으로 순위가 달라졌는지 탐지한다.
- 사용자가 평가하지 않은 영화를 임의의 정답 또는 오답으로 확정하지 않는다.

### 비교한 정량 지표

| 지표 | 측정 대상 | 장점 | 한계 | 현재 용도 |
| --- | --- | --- | --- | --- |
| `Precision@K` | Top-K 중 선호 영화의 비율 | 추천 목록의 관련도 밀도를 직접 확인할 수 있음 | 관련 영화가 많은 사용자를 충분히 회수했는지는 알 수 없음 | 보조 지표 후보 |
| `Recall@K` | holdout의 선호 영화 중 Top-K가 회수한 비율 | 사용자가 좋아한 영화를 놓치지 않는지 확인할 수 있음 | Top-K 내부의 순서와 평점 차이를 반영하지 않음 | 현재 보조 지표 |
| `HitRate@K` | Top-K에 선호 영화가 하나라도 있는지 여부 | 해석이 단순하고 사용자 단위 성공률을 보기 쉬움 | 하나를 맞힌 뒤의 순위 품질과 추가 적중을 구분하지 못함 | 보조 지표 후보 |
| `MRR@K` | 첫 번째 선호 영화가 등장한 순위 | 최상단에서 얼마나 빨리 관련 영화를 제시하는지 측정함 | 첫 적중 이후의 추천 결과를 평가하지 않음 | 보조 지표 후보 |
| `NDCG@K` | 평점별 relevance와 순위 위치 | 더 선호한 영화를 더 위에 배치했는지 정량화할 수 있음 | 동일한 relevance 정의와 후보 집합에서만 직접 비교해야 함 | 현재 주 지표 |

**최종 선택:** `NDCG@20%`를 주 지표로, `Recall@20%`를 보조 지표로 사용한다. NDCG는 평점 차이와 순위 위치를 함께 반영하므로 더 선호한 영화를 상단에 배치하는 능력을 측정하고, Recall은 선호 영화 자체를 Top-K에서 누락하는 문제를 보완한다. 현재 `final_score`는 순위 품질을 우선해 `0.8 × NDCG + 0.2 × Recall`로 계산한다.

### 비교한 holdout 방식

| 방식 | 장점 | 한계 | 현재 용도 |
| --- | --- | --- | --- |
| 무작위 train/holdout 분할 | 구현이 단순하고 데이터 비율을 안정적으로 맞출 수 있음 | 미래 기록이 train에 포함되는 시간 누수가 발생할 수 있음 | 사용하지 않음 |
| leave-one-out | 사용자 기록 대부분을 학습에 사용할 수 있고 평가가 단순함 | 정답이 한 편이라 사용자 취향의 세부 순위 품질을 평가하기 어려움 | 사용하지 않음 |
| 시간순 70/30 holdout | 과거 기록으로 이후 선호를 예측하는 실제 추천 시점을 모사함 | 기록이 적은 사용자는 train과 holdout이 작아 지표 변동성이 커질 수 있음 | 현재 분할 기준 |
| 전체 카탈로그 Top-K 평가 | 후보 검색과 최종 ranking을 함께 평가할 수 있음 | 미평가 영화의 선호 여부를 알 수 없고 후보 모집단 변화가 점수에 영향을 줌 | 추후 retrieval 보조 평가 |
| 고정 holdout 후보 ranking | 모든 엔진에 동일한 평가 후보를 제공해 점수화·정렬 성능을 직접 비교함 | 전체 카탈로그에서 관련 영화를 찾아내는 retrieval 성능은 측정하지 못함 | 현재 평가 프로토콜 |

**최종 선택:** 사용자 기록을 시간순으로 나눈 `train 70% / holdout 30%`와 고정 holdout 후보 ranking을 사용한다. 70%는 취향 프로필을 구성할 학습 기록을 충분히 보존하고, 30%는 사용자별 NDCG와 Recall을 계산할 평가 영화를 확보하기 위한 절충이다. 시간순 분할로 미래 기록의 학습 유입을 막고, 모든 엔진에 동일한 holdout 후보를 제공해 후보 탐색량이 아닌 개인화 점수화·정렬 성능을 비교한다. 전체 카탈로그 retrieval 성능은 현재 점수와 섞지 않고 별도 평가로 확장한다.

## 지표와 판단 기준

사용자마다 `K = ceil(holdout 영화 수 × 20%)`로 계산한다. 사용자별 평가 영화 수가 다르기 때문에 고정 K 대신 같은 비율을 적용한다.

- `coverage`: holdout 후보 중 엔진이 반환한 영화 비율. 누락이나 연결 오류를 진단한다.
- `Recall@20%`: 평점 3.5 이상인 선호 영화 중 Top-K에 들어온 비율이다.
- `NDCG@20%`: 평점에 등급을 부여해 더 좋아한 영화를 위에 배치했는지 측정한다.
  - 3.5점 → relevance 1
  - 4.0점 → relevance 2
  - 4.5점 → relevance 3
  - 5.0점 → relevance 4
  - 3.0점 이하 → relevance 0
- `final_score = 0.8 × NDCG + 0.2 × Recall`

NDCG 비중을 높인 이유는 이 평가의 핵심이 단순 포함 여부보다 **사용자가 더 선호한 영화를 더 위에 배치하는 능력**이기 때문이다. Recall은 선호 영화 자체를 놓치는 문제를 보조 반영한다. 가중치를 변경하면 과거 점수와 다른 평가 체제가 되므로 새 지표 버전과 기준선을 선언해야 한다.

결과를 비교할 때는 다음 조건을 지킨다.

1. 같은 dataset version끼리만 직접 비교한다.
2. cases/cohorts SHA-256이 같은지 확인한다.
3. DB catalog SHA-256과 활성 ontology build가 같은지 확인한다.
4. 평균만 보지 않고 cohort별·사용자별 지표 변화를 확인한다.
5. 엔진 또는 지표 변경이 없는 리팩터링은 사용자별 결과 전체가 동일해야 통과한다.

`fixed-v1`의 현재 회귀 기준 final score 평균은 다음과 같다.

| 엔진 | 10 | 50 | 100 | 150 | 200 | 500 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V1 | 0.3357768174 | 0.3643516380 | 0.3124535792 | 0.3241955830 | 0.3173024839 | 0.3314067424 |
| V2 | 0.3781088430 | 0.3301367641 | 0.3009557019 | 0.3036205583 | 0.2993517734 | 0.3114505423 |

DB catalog나 ontology build가 달라지면 같은 코드라도 점수가 달라질 수 있으므로, 표의 숫자만 단독으로 정답으로 사용하지 않는다. 결과 JSON의 provenance가 함께 일치해야 한다.

## 실제 엔진을 사용하는 범위

호출 경로는 다음과 같다.

```text
evaluation
  → app.services.recsys.registry
  → 선택 버전 adapter
  → 선택 버전 evaluation engine
  → 실제 엔진의 profile / candidate / scorer / ranker 함수
```

- V1은 실제 worker의 preference profile, 콘텐츠 가중치와 cosine similarity를 사용한다.
- V2는 실제 `profile_builder`, `candidate_generator`, `scorer`, `ranker`를 사용한다.
- `evaluation` 안에는 V1/V2 점수식이나 엔진 버전 분기가 없다.

다만 이 평가는 실서비스 HTTP 요청이나 전체 카탈로그 후보 retrieval을 그대로 재생하는 종단 테스트는 아니다. 공정한 비교를 위해 후보 모집단을 동일한 holdout 영화로 고정하고, 실제 엔진의 후보 점수화·정렬 계층에 전달한다. 따라서 “실제 엔진의 고정 후보 ranking 평가”라고 표현하는 것이 정확하다.

## 결과 파일

```text
evaluation_results/<테스트-이름>/<실행시각>.json
evaluation_results/<테스트-이름>/<실행시각>.summary.csv
```

JSON에는 다음이 들어간다.

- 엔진 이름과 평가 구현 버전
- 시작·종료 시각과 총 실행 시간
- dataset manifest 및 cases/cohorts/movie identities SHA-256
- Git commit, dirty 여부와 평가 source tree SHA-256
- Alembic 버전
- 평가 영화 catalog SHA-256
- 활성 ontology build ID, version, source hash, node/edge 수
- cohort별 summary와 사용자별 지표

CSV는 테스트 이름, 엔진, 시각, cohort별 평균 지표만 빠르게 비교할 때 사용한다. 상세한 재현 및 원인 분석의 기준은 JSON이다.

## 고정 데이터와 새 버전 생성

일반 평가 실행에는 `ml-32m.zip`이나 별도 mapping CSV가 필요하지 않다. 평가에 필요한 사용자 목록, train/holdout, TMDB 안정 식별자 스냅샷과 manifest가 저장소에 포함되어 있다.

고정 데이터의 영화는 생성 당시 DB ID와 `tmdb_id`를 함께 보존한다. 평가를 실행할 때 `tmdb_id`로 현재 DB 영화를 먼저 확인하므로, DB를 다시 적재해 내부 ID만 바뀐 경우에는 현재 ID로 자동 재매핑한다. 재매핑 건수와 `이전 DB ID → TMDB ID → 현재 DB ID` 내역은 결과 JSON의 `runtime.movie_identity_resolution`에 기록된다.

동일한 TMDB 영화가 현재 DB에 아예 없으면 평가는 누락 영화를 조용히 제외하지 않고 시작 전에 중단한다. 영화를 제외하면 train/holdout과 과거 점수 기준이 달라지기 때문이다. 이 경우 동일 영화를 DB에 복원하거나, 결측을 반영한 새 dataset 버전을 생성해야 한다. 영화 ID는 같지만 장르·키워드·배우·온톨로지 등의 내용이 달라진 경우에는 catalog SHA-256이 달라져 결과에서 확인할 수 있다.

기존 `fixed-v1`은 덮어쓰지 않는다. 사용자, 분할, 영화 매핑을 변경하려면 새 버전을 만든다.

```powershell
docker compose exec -T back-api python -m evaluation.prepare_cases `
  --dataset "/back/ml-32m.zip" `
  --version fixed-v2
```

위 명령을 Docker에서 실행하려면 zip이 잠시 `/back/ml-32m.zip`, 즉 `VIDEO_BACK` 루트에 있어야 한다. 생성이 끝난 뒤 zip은 필요 없으며 커밋하지 않는다.

생성 결과:

```text
evaluation/data/fixed-v2/cases.jsonl.gz
evaluation/data/fixed-v2/cohorts.json
evaluation/data/fixed-v2/movie_identities.json.gz
evaluation/data/fixed-v2/manifest.json
```

사용자 목록도 변경하려면 새 cohort JSON을 만든 뒤 다음처럼 지정한다.

```powershell
docker compose exec -T back-api python -m evaluation.prepare_cases `
  --dataset "/back/ml-32m.zip" `
  --version fixed-v2 `
  --cohorts-source "/back/evaluation/cohorts_v2.json"
```

새 데이터는 V1부터 모든 사용 가능한 엔진을 다시 실행해 새 기준선을 만든다.

```powershell
docker compose exec -T back-api python -m evaluation "fixed-v2-v1" --engine v1 --dataset fixed-v2
docker compose exec -T back-api python -m evaluation "fixed-v2-v2" --engine v2 --dataset fixed-v2
```

## 새 엔진 버전 연결

registry는 `app.services.recsys.v<번호>.adapter`를 동적으로 불러오므로 V4, V5를 추가할 때 `evaluation`이나 registry를 수정하지 않는다.

새 adapter는 서비스 계약과 평가 엔진 생성 계약을 구현하고, 파일 끝에 공통 이름을 공개한다.

```python
# app/services/recsys/v4/adapter.py
class V4RecommendationAdapter:
    name = "v4"
    max_page_size = 100

    def get_recommendations(self, db, query):
        return actual_v4_recommend(db, query)

    def refresh_cold_start(self, db, user_id):
        actual_v4_refresh(db, user_id)

    def create_evaluation_engine(self):
        from app.services.recsys.v4.evaluation import V4EvaluationEngine
        return V4EvaluationEngine()


RecommendationAdapter = V4RecommendationAdapter
```

평가 엔진의 필수 계약은 세 메서드뿐이다.

```python
class V4EvaluationEngine:
    name = "v4"
    version = "v4-fixed-cohort"

    def prepare(self, inputs):
        self.state = actual_v4_prepare(inputs)

    def rank(self, input_data):
        return actual_v4_rank(
            self.state,
            user_id=input_data.user_id,
            candidate_movie_ids=input_data.candidate_movie_ids,
        )

    def close(self):
        actual_v4_close(self.state)
```

- `prepare`: 전체 cohort의 train 기록으로 profile, cohort context 또는 평가용 모델을 한 번 준비한다.
- `rank`: 입력된 후보 ID만 실제 엔진 로직으로 순위화해 영화 ID 목록을 반환한다.
- `close`: 임시 모델, 세션과 자원을 정리한다.

V1/V2처럼 즉시 계산하는 엔진은 `prepare`가 가볍다. LightFM 같은 학습형 엔진은 `prepare`에서 고정 train 데이터로 평가용 모델을 만들거나 검증된 artifact를 로드한다. 이 차이는 엔진 내부에만 있고 공통 실행기와 지표는 바뀌지 않는다.

## 파일별 역할

| 파일 | 역할 | 유지 이유 |
| --- | --- | --- |
| `evaluation/__main__.py` | CLI 인자 해석, 엔진·dataset 선택, 공통 benchmark 시작 | 모든 버전의 단일 전원 버튼 |
| `evaluation/benchmark.py` | cohort 반복, 병렬 사용자 평가, NDCG/Recall, 진행도, JSON·CSV 저장 | 평가의 핵심 실행기. 삭제 대상이 아님 |
| `evaluation/contracts.py` | 앱의 공통 평가 타입을 짧은 이름으로 가져옴 | 평가 코드가 앱 타입 이름에 종속되는 범위를 한 곳으로 제한 |
| `evaluation/engine.py` | registry에서 선택 엔진의 평가 인스턴스 생성 | evaluation 내부 버전 분기 제거 |
| `evaluation/datasets.py` | dataset 경로 규칙, TMDB 식별자 검증·DB ID 재매핑 | 데이터 버전 분리와 DB 재적재 대응 |
| `evaluation/prepare_cases.py` | MovieLens zip에서 TMDB 기준으로 새 고정 dataset 생성 | 평상시 실행에는 불필요하지만 데이터 변경 시 필요 |
| `evaluation/provenance.py` | Git, DB schema, catalog, ontology 상태와 해시 수집 | 서로 다른 환경 결과를 같은 기준으로 오인하는 문제 방지 |
| `evaluation/cohorts.json` | 기존 fixed-v1의 10/50/100/150/200/500명 사용자 목록 | 과거 사용자 기준 보존 |
| `evaluation/data/fixed_cases.jsonl.gz` | fixed-v1의 사용자별 train/holdout | zip 없이 반복 평가 가능 |
| `evaluation/data/fixed_movie_identities.json.gz` | fixed-v1 평가 영화의 생성 당시 DB ID와 TMDB ID | DB 내부 ID가 바뀌어도 동일 영화를 복구 |
| `evaluation/data/fixed_cases_manifest.json` | fixed-v1 생성 조건과 해시 | 데이터 변조·분할 변경 탐지 |
| `evaluation/README.md` | 평가 기준, 실행, 확장과 파일 책임 설명 | 팀 공통 운영 문서 |
| `evaluation/__init__.py` | Python package 선언 | `python -m evaluation` 실행에 필요 |

과거 `ontol_test`의 MovieLens↔DB 매핑 책임은 TMDB 식별자 스냅샷과 실행 전 재매핑으로 이관했다. 따라서 과거 V2 전용 실행기, 중복 MovieLens CSV와 개별 JSONL 결과는 공통 실행 경로에서 제거했다. 기준 비교에 필요한 최신 결과는 `evaluation_results`에 남으며, 해당 디렉터리는 실행 산출물이므로 Git에는 포함하지 않는다.
