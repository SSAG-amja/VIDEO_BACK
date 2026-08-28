# 02. V3 구현 방식

## 1. 문서 책임

이 문서는 V3를 어떤 패키지와 데이터 흐름으로 구현하는지 정의한다.

- 전체 구조와 파이프라인: [01 아키텍처와 파이프라인](01_architecture_and_pipeline.md)
- 서비스 정책: [03 추천 정책](03_recommendation_policy.md)
- 조정값: [04 LightFM 조정 지점](04_lightfm_tuning.md)
- 그래프 schema: [05 온톨로지 구조](05_ontology_structure.md)

## 2. 아키텍처

### 2.1 오프라인 경로

```text
PostgreSQL snapshot
  -> behavior dataset builder
  -> ontology feature exporter
  -> sparse interaction/user/item feature matrix
  -> LightFM trainer
  -> model artifact + manifest
  -> blockwise top-150 candidate storage materializer
  -> candidate snapshot
```

오프라인 작업은 FastAPI request context에 의존하지 않는다.

### 2.2 온라인 경로

```text
HTTP API v1
  -> recommendation service registry
  -> V3 recommender
       -> active serving bundle
       -> materialized LightFM candidates
       -> short-term ontology candidates
       -> ontology analyzer
       -> policy engine
       -> explainer
  -> 기존 RecommendationResponse
```

API는 엔진별 import나 분기를 소유하지 않는다. API V1은 HTTP 계약 버전이며 추천 엔진 V1과 다른 개념이다.

## 3. 패키지 구조

```text
app/services/recsys/
  contracts.py
  registry.py
  v1/adapter.py
  v2/adapter.py
  v3/adapter.py

app/services/recsys/v3/
  README.md
  adapter.py
  config.py
  errors.py
  recommender.py
  domain/
    behavior.py
    catalog.py
    feature_registry.py
    ontology_registry.py
    schemas.py
  profiles/
    profile_builder.py
  retrieval/
    candidate_eligibility.py
    candidate_merger.py
    eligibility_schemas.py
    lightfm_retriever.py
    ontology_analyzer.py
    retrieval_pipeline.py
    retrieval_schemas.py
    score_normalizer.py
    short_term_candidate_cache.py
    short_term_refresh_policy.py
    short_term_retriever.py
  cold_start/
    cold_start_merger.py
    cold_start_pipeline.py
    cold_start_retriever.py
  policy/
    policy_config.py
    policy_engine.py
    policy_registry.py
    policy_schemas.py
    quality.py
  serving/
    model_store.py
    serving_bundle.py

app/jobs/recsys/v3/
  README.md
  datasets/
    dataset_builder.py
    dataset_schemas.py
    social_signal_projector.py
  features/
    feature_builder.py
    feature_schemas.py
    user_feature_builder.py
  training/
    artifact_publisher.py
    model_schemas.py
    trainer.py
    train_identity_model.py
    train_hybrid_model.py
  candidates/
    candidate_materializer.py
    candidate_publisher.py
    candidate_schemas.py
    candidate_snapshot.py
    materialize_candidates.py
  ontology/
    ontology_asset_validator.py
    ontology_build_pipeline.py
    ontology_graph_builder.py
  serving/
    serving_bundle_publisher.py
  workers/
    short_term_candidate_worker.py
    worker.py
  diagnostics/
    lightfm_dependency_spike.py
    parallel_build_benchmark.py
    item_feature_export_diagnostics.py

app/crud/recsys/
  model build, serving bundle, candidate snapshot 접근 코드
```

`app/services/recsys/v3`는 온라인 계층이며 `app/jobs/recsys/v3`를 import하지 않는다. 공통 행동 계약과 시간 정규화는 `services/v3/domain/behavior.py`, model·ontology·serving이 공유하는 catalog eligibility는 `services/v3/domain/catalog.py`가 소유한다. `jobs/v3/diagnostics`는 수동 실행 전용이며 운영 worker나 scheduler가 import하지 않는다.

현재 S7 온라인 retrieval 구현은 `retrieval_schemas.py`, `short_term_retriever.py`, `score_normalizer.py`, `candidate_merger.py`, `ontology_analyzer.py`, `retrieval_pipeline.py`에 있다. S8은 `policy_schemas.py`, `policy_engine.py`, `quality.py`, `cold_start_retriever.py`, `cold_start_merger.py`, `cold_start_pipeline.py`에 있다. 이 모듈들은 job artifact나 API를 import하지 않으며 model 후보와 runtime profile을 명시적 입력 계약으로 받는다. 영화 metadata 원시 조회는 `app/crud/recsys/movies.py`가 소유한다.

S8 policy engine은 순위 후보 최대 150개의 metadata·최신 OTT 자격을 set-based로 먼저 확인하고, 통과 순서의 최대 100개에 대해서만 ontology와 MMR feature 집합을 조회한다. LightFM raw score, source별 normalized score, ontology type score, 정책별 가감점, MMR 효과와 최종 점수를 한 객체에 덮어쓰지 않고 trace 필드로 분리한다. 추천 이유는 ontology/policy 관측이며 LightFM 인과 attribution이 될 수 없다.

Cold-start 경로는 다음 순서를 사용한다.

```text
LightFM feature-only 후보(있을 때)
+ onboarding favorite/genre graph 역조회 후보
-> source별 percentile 정규화와 최대 150개 병합
-> 저비용 hard filter 후 최대 100개 활성화
-> model mapping 밖 graph 영화를 ontology_cold_item으로 표시
-> graph 후보가 없을 때만 reliable quality fallback
-> 공통 analyzer와 policy engine
```

현재 V3 ontology 구현 위치:

```text
app/services/recsys/v3/domain/ontology_registry.py
  node/relation endpoint와 consumer 계약

app/jobs/recsys/v3/ontology/ontology_graph_builder.py
  catalog, factual node/edge, semantic evidence, canonical 집계, validation

app/jobs/recsys/v3/ontology/ontology_build_pipeline.py
  asset/DB fingerprint, immutable build lifecycle, 성공 처리 경계

app/jobs/recsys/v3/ontology/ontology_asset_validator.py
  asset endpoint/중복/범위/설명/concept path와 DB source coverage 검증

app/jobs/recsys/v3/features/feature_builder.py
  registry 기반 sparse item feature CSR, frequency pruning, deterministic manifest

app/jobs/recsys/v3/features/feature_schemas.py
  pruning rule, family diagnostics, build-bound export 계약

app/models/ontology.py
app/crud/recsys/ontology.py
app/db/alembic/versions/a3c5e7f9b1d2_add_ontology_v3_schema_boundary.py
  V2/V3 schema 범위와 evidence 저장 계약
```

graph pipeline은 feature exporter 검증 전에는 V3 build를 active로 전환하지 않는다. Build `22`의 full graph와 전체 feature export는 통과했지만, ontology를 단독 활성화하지 않고 model·ontology·policy를 하나의 검증된 serving bundle로 전환한다. 실측 diagnostics는 `z_v3_docs/diagnostics/item_feature_export_build_22.json`에 저장한다.

Item Feature Exporter는 movie identity를 모든 행에 유지하고 `genre`, `keyword`, `actor`, `director`, `theme`, `mood`만 CSR column으로 내보낸다. actor/director/keyword는 movie frequency pruning을 적용하고 semantic relation은 `effective_strength`를 값으로 사용한다. OTT availability는 exporter 대상이 아니며 serving policy가 최신 DB 상태를 읽는다. 결과 manifest는 ontology build ID/schema/source hash, mapping hash, pruning 설정과 feature별 drop/coverage/nnz를 기록한다.

Runtime Profile Builder는 LightFM feature matrix를 역해석하지 않는다. 직접 행동과 해당 영화의 V3 graph edge를 읽어 별도 positive/negative profile을 만들며, 각 점수에 action/time/decay/edge provenance를 남긴다. graph 조회는 사용자 행동 영화 ID를 배열로 전달하는 단일 set-based query이고 관계군은 `has_genre`, `has_keyword`, `has_actor`, `has_director`, `has_theme`, `has_mood`로 제한한다. inactive graph를 serving에 자동 연결하지 않으며 호출자가 검증할 ontology build ID를 명시해야 한다.

영화 metadata 변경 시 graph 재빌드 여부와 필수 자동 갱신 orchestration은 [05 온톨로지 구조](05_ontology_structure.md)의 `9.4.1 Graph 재빌드 판정`을 따른다. 모든 metadata 변경이 재빌드 대상은 아니며, 현재 `movies.overview` 변경은 semantic signal 선행 갱신이 필수다. 향후 startup과 movie updater 완료 시 변경을 자동 감지해 필요한 추출·build를 실행하는 로직을 구현해야 한다.

V3 graph build는 다음 최적화를 적용한다.

```text
build ID 전용 공유 UNLOGGED movie catalog
catalog ordinal/실제 movie ID 기반 1,000편 factual edge chunk
4 worker가 완료 즉시 다음 chunk를 가져가는 동적 queue
has_genre/keyword/actor/director/OTT factual relation 병렬 처리
relation별 독립 stage/rows/elapsed/commit metric
worker별 chunk/rows/active elapsed metric
대용량 semantic stage work_mem 64MB
재생성 가능한 비활성 build stage의 asynchronous commit
worker 실패 시 queue 중단과 공유 staging cleanup
node/factual edge/semantic 완료 시 planner statistics 갱신
기존 unique/prefix index와 중복되는 5개 index 제거
```

## 4. 엔진 plugin 경계

공통 입력:

```python
RecommendationQuery(
    user_id=...,
    mode=...,
    limit=...,
    offset=...,
    shuffle_seed=...,
)
```

공통 출력:

```text
RecommendationResponse
  user_id
  mode
  movie_ids
  limit / offset / count / has_more
  source
```

엔진 선택:

```text
RECOMMENDATION_ENGINE=v1 | v2 | v3
```

registry는 선택된 adapter만 lazy import한다. V3 adapter는 완성된 serving pipeline을 호출하며, 검증된 active bundle이 없을 때만 `V3NotReadyError`를 발생시킨다. V1 구현을 V3처럼 반환하지 않는다.

## 5. 학습 데이터 구현

### 5.1 행동 원천과 구현 상태

| 신호 | 원천 | 시간 정보 | 현재 builder |
| --- | --- | --- | --- |
| saved | `playlist_movies` + 소유 `playlists` | `playlist_movies.created_at` | 구현 |
| pinned | `user_interactions` | `pinned_at` | 구현 |
| watched | `user_interactions` | `watched_at` | 구현 |
| passed | `user_interactions` | `passed_at` | 구현 |
| onboarding favorite | `user_favorite_movies` | 없음 | 구현 |
| movie post write | `posts.movie_id` | `posts.created_at` | raw 추출 구현, 학습 제외 |
| playlist post write | `posts.playlist_id` + `playlist_movies` | post와 item 생성 시각 | raw `1/N` 투영 구현, 학습 제외 |
| movie post like | `likes` + `posts.movie_id` | likes 시각 없음 | 현재 시점 raw 추출 구현, 학습 제외 |
| playlist post like | `likes` + `posts.playlist_id` | likes 시각 없음 | 보류 |
| movie post reply | `replies` + `posts.movie_id` | `replies.created_at` | raw 추출 구현, 학습 제외 |
| playlist post reply | `replies` + `posts.playlist_id` | reply와 item 생성 시각 | raw `1/N` 투영 구현, 학습 제외 |
| onboarding genre | `user_genres` | 없음 | user feature 예정 |
| OTT 구독 | `user_otts` | 없음 | serving context와 rule filter |

현재 테이블은 immutable event log가 아니라 최신 상태 snapshot이다. 상태 해제 이력, 정확한 반복 횟수, 과거 행동 순서를 복원하지 않는다.

현재 `likes`는 게시글 좋아요이며 영화 좋아요 테이블이 아니다. `saved`는 사용자가 소유한 playlist의 `playlist_movies`로부터 만들어진다.

### 5.2 현재 Dataset builder

`app/jobs/recsys/v3/datasets/dataset_builder.py`는 다음 set-based query를 사용한다.

1. non-adult 학습 catalog
2. onboarding favorite
3. 사용자-영화별 최신 saved 시각
4. pinned/watched/passed 현재 상태

출력:

```text
sorted user/movie ID mapping
positive interactions COO
sample weights COO
positive provenance
social raw signal/provenance
social projection diagnostics/hash
passed movie set
watched movie set
serving exclusion set
dataset diagnostics/hash
```

interaction과 sample weight COO는 동일한 row/column 좌표를 사용한다. passed는 WARP interaction에 포함하지 않는다.

### 5.3 Social signal projector

게시글·좋아요·댓글은 먼저 user-movie signal로 투영한 뒤 dataset builder의 공통 집계기에 전달한다.

```text
fixed movie post
  post/reply/like user + posts.movie_id
  -> 한 user-movie signal

playlist post write
  PlaylistMovie.created_at <= Post.created_at
  -> 당시 확인 가능한 N개 영화에 각 1/N unit

playlist post reply
  PlaylistMovie.created_at <= Reply.created_at
  -> 당시 확인 가능한 N개 영화에 각 1/N unit
```

구현 원칙:

- source별 set-based query를 사용하고 user 또는 post별 N+1 query를 만들지 않는다.
- 삭제된 과거 playlist item은 복원할 수 없음을 diagnostics에 기록한다.
- self-like와 자기 게시글 reply는 중복 신호에서 제외한다.
- 동일 user-movie-source의 units를 먼저 합치고 로그 포화한 뒤 pair 집계를 수행한다.
- projection source, source object ID, event time, distributed unit을 provenance에 남긴다.
- `likes.created_at`이 추가되기 전에는 playlist post like를 학습에 넣지 않는다.
- `likes.created_at`이 없을 때 movie post like는 현재 시점 snapshot에만 포함하고 과거 cutoff dataset에서는 제외한다.

향후 eligible social signal을 positive pair에 연결할 때는 대표 행동만 남기지 않고 어떤 직접·파생 source가 기여했는지 함께 저장한다.

현재 구현은 모든 social raw signal에 `eligible_for_training=false`, `eligibility_reason=direction_unresolved`를 설정한다. 따라서 projector 실행만으로 기존 LightFM interaction이나 sample weight가 바뀌지 않는다.

### 5.4 향후 event log

운영형 V3에서는 다음 불변 이벤트가 필요하다.

```text
saved / unsaved
pinned / unpinned
watched / unwatched
passed / unpassed
post_created / post_deleted
post_liked / post_unliked
reply_created / reply_deleted
playlist_item_added / playlist_item_removed
exposed
short_dwell / long_dwell
```

새 event log는 현재 상태 테이블을 대체하지 않고 학습 및 진단 원천으로 추가한다.

최소 schema 보완은 `likes.created_at`과 playlist 구성 변경 이벤트다. 그래야 플레이리스트 게시글에 반응한 시점의 영화 구성을 정확히 재현할 수 있다.

## 6. Feature registry

구현 위치: `app/services/recsys/v3/domain/feature_registry.py`, `app/services/recsys/v3/domain/schemas.py`

각 feature는 원천부터 consumer까지 연결 상태를 선언한다.

```text
feature name
namespace
source table/build
required | optional | disabled
LightFM item consumer
LightFM user consumer
runtime profile consumer
ontology evidence consumer
policy consumer
explanation consumer
retained/dropped diagnostics
```

필수 경로:

| feature | item | user | runtime profile | evidence | policy/reason |
| --- | ---: | ---: | ---: | ---: | ---: |
| genre | 필수 | 필수 | 필수 | 필수 | 필수 |
| keyword | 필수 | 선택 | 필수 | 필수 | 필수 |
| actor | 필수 | 선택 | 필수 | 필수 | 필수 |
| director | 필수 | 선택 | 필수 | 필수 | 필수 |
| theme | 필수 | 선택 | 필수 | 필수 | 필수 |
| mood | 필수 | 선택 | 필수 | 필수 | 필수 |
| OTT | 비활성 | 비활성 | serving context | 필수 | 필수 |

OTT 구독과 영화 제공 여부는 LightFM user/item feature로 사용하지 않는다. `subscribed_only` hard filter와 `all` 모드 정책, availability 설명은 항상 최신 `user_otts`와 `movie_otts`를 사용한다.

`선택`은 데이터 경로를 생략한다는 의미가 아니다. LightFM 입력 사용 여부만 별도 build로 결정한다.

source readiness는 `available`, `pending_v3_ontology`, `reference_only`로 분리한다. V2 graph와 asset은 `reference_only`이며 V3 serving source로 활성화하지 않는다.

## 7. LightFM 구현 경계

기본 모델:

```text
user representation
  user identity embedding
  + 선택된 onboarding/ontology user feature

movie representation
  movie identity embedding
  + ontology item feature
```

identity feature는 제거하지 않는다. 협업 신호는 identity가 담당하고 희소·신규 영화 일반화는 ontology feature가 담당한다.

LightFM에는 graph 객체가 아니라 고정 namespace의 sparse matrix만 전달한다. 가중치와 hyperparameter는 [04 LightFM 조정 지점](04_lightfm_tuning.md)에서 관리한다.

## 8. Model artifact

권장 구조:

```text
assets/ml_models/v3/{model_build_id}/
  model.joblib
  manifest.json
  user_mapping.json 또는 binary mapping
  item_mapping.json 또는 binary mapping
  user_feature_mapping.json
  item_feature_mapping.json
  config.json
  diagnostics.json
```

manifest 필수 값:

```text
model build ID/version
created_at/data_cutoff_at
dataset hash
ontology build ID/schema version/source hash
policy/config hash
random seed/loss/hyperparameters
user/movie/feature/interactions dimensions
package versions
```

학습 중인 디렉터리를 serving이 읽지 않는다. artifact 저장과 재로딩이 끝난 build만 게시한다.

S3 identity-only artifact는 `model.joblib`, `user_ids.npy`, `movie_ids.npy`, `config.json`, `diagnostics.json`, `manifest.json`을 저장한다. ontology feature mapping은 포함하지 않고 manifest에 `ontology.applicable=false`를 기록한다. 행동 weight·최근성·passed/social 처리 정책은 training data policy snapshot과 hash로 고정한다. 모든 파일 hash, mapping/model 차원, 저장 전후 prediction hash가 일치한 경우에만 임시 디렉터리를 최종 build ID로 원자적으로 변경한다.

실행 명령:

```bash
python -m app.jobs.recsys.v3.training.train_identity_model
```

positive interaction이 없는 DB에서는 명확한 오류로 종료하며 artifact를 만들지 않는다.

S5 hybrid artifact에는 아래 파일이 추가된다.

```text
user_features.npz
item_features.npz
user_feature_tokens.joblib
item_feature_tokens.joblib
user_feature_manifest.json
item_feature_manifest.json
```

hybrid manifest는 ontology build ID/status/schema/source hash, item/user export hash, parent item export hash, entity/feature mapping hash와 feature registry version을 고정한다. LightFM의 user feature embedding과 item feature embedding은 서로 다른 parameter 공간이므로 같은 `genre:{id}` 문자열을 쓴다는 이유만으로 같은 vector가 되지는 않는다. 각 token은 source 추적과 mapping 호환을 위해 동일 namespace를 사용하며 실제 관계는 interaction 학습으로 형성된다.

실행 명령:

```bash
python -m app.jobs.recsys.v3.training.train_hybrid_model 22
```

dataset에 positive user가 없으면 78초 규모의 item export를 다시 실행하기 전에 종료한다. hybrid artifact 게시만으로 serving bundle이나 ontology active 상태를 변경하지 않는다.

## 9. Serving bundle

serving bundle은 다음 호환 단위를 원자적으로 묶는다.

```text
model_build_id
ontology_build_id
policy_config_version
candidate_snapshot_version
feature_registry_version
```

새 ontology build만 단독 활성화하거나 새 모델을 다른 feature mapping과 연결하지 않는다. 로딩 실패 시 현재 정상 bundle을 유지한다.

API process는 artifact를 요청마다 읽지 않고 memory cache한다. 교체는 명시적 reload 또는 안전한 polling으로 수행한다.

현재 구현은 `assets/ml_models/v3/active_bundle.json`을 유일한 online 전환점으로 사용한다. `serving_bundle_publisher.py`는 hybrid artifact 전체 hash/차원, ontology schema/source/status, candidate snapshot의 model ID와 DB 게시 건수, feature registry, policy config hash를 먼저 검증한다. immutable `serving_bundles/{bundle_id}/manifest.json`을 만든 뒤 V3 ontology 활성 표시를 갱신하고 pointer를 원자 교체한다. V2 graph 활성 상태는 변경하지 않는다.

`services/v3/serving/serving_bundle.py`는 pointer가 바뀐 경우에만 model artifact를 다시 읽는다. 새 bundle 검증에 실패하면 이미 메모리에 있는 직전 정상 bundle을 계속 사용한다. 새 process에 정상 bundle이 하나도 없으면 `V3NotReadyError`를 발생시킨다.

활성화 명령:

```bash
python -m app.jobs.recsys.v3.serving.serving_bundle_publisher \
  assets/ml_models/v3/{model_build_id} \
  assets/ml_models/v3/candidate_snapshots/{candidate_snapshot_id}
```

이 명령 전에 candidate snapshot을 `--publish`로 DB에 게시해야 한다. 현재 검증된 활성 pointer와 artifact ID는 [README](README.md)에 기록하며, 새 조합은 전체 호환성 검증을 통과한 경우에만 교체한다.

## 10. 후보 materialization

전체 `users x movies` dense score matrix를 만들지 않는다.

```text
for user block:
  for item block:
    LightFM score 계산
    user별 bounded top-K heap 갱신
  user별 top-150 snapshot 저장
```

요구사항:

- user/item mapping은 model artifact와 동일해야 함
- watched/passed exclusion을 적용 가능해야 함
- 사용자별 실패가 다른 사용자 snapshot을 훼손하지 않아야 함
- 새 run 전체가 완료되기 전 이전 정상 후보 유지
- model score와 source rank를 저장

현재 구현은 user block `32`, item block `8,192`, checkpoint `1,024 users`, 저장 top-K `150`을 기본값으로 사용한다. 이 중 100개만 상세 분석 대상이며 다음 50개는 hard filter 보충용 예비 후보다. `candidate_materializer.py`는 LightFM user/item representation으로 block score만 만들고 동점은 movie ID로 결정한다. `candidate_snapshot.py`는 입력·제외 목록 hash에 묶인 checkpoint shard를 검증한 뒤 불변 디렉터리로 원자 게시한다.

`app/crud/recsys/recommendations.py`는 현재 존재하고 탈퇴하지 않은 artifact 사용자와 watched/passed exclusion 조회, 기존 추천 행 delete/insert를 담당한다. `candidate_publisher.py`는 이를 조합해 게시 직전에 eligible 사용자와 exclusion hash를 다시 계산하고 materialization 이후 변경이 있으면 중단한다. 완성된 snapshot 게시 시 성공 사용자만 교체하며 함수 내부에서는 commit하지 않는다. 따라서 호출자는 검증과 전체 게시를 한 트랜잭션으로 확정하고, 오류 시 rollback해 이전 정상 후보를 유지한다. 실패 사용자는 delete 대상에 포함하지 않는다.

실행 진입점:

```bash
python -m app.jobs.recsys.v3.candidates.materialize_candidates \
  assets/ml_models/v3/{model_build_id} \
  --publish
```

`--publish`를 생략하면 검증된 candidate snapshot까지만 만들고 DB는 변경하지 않는다.

## 11. Online ontology 처리

온라인에서는 다음을 금지한다.

- 전체 ontology graph scan
- 후보 영화마다 개별 graph query
- 전체 후보에 대한 무거운 explanation JSON 선계산

처리 순서:

1. 저비용 hard filter를 통과한 최대 100개 후보 ID를 bounded set으로 전달한다.
2. 사용자 profile feature를 `VALUES`, `unnest` 또는 임시 테이블로 전달한다.
3. set-based query로 후보 edge만 집계한다.
4. 유형별 숫자 점수로 먼저 재정렬한다.
5. 최종 선택 후보만 상세 evidence path를 조회한다.

S7 analyzer는 4번까지 구현한다. profile feature는 `relation_type`, registry의 정확한 `ontology_node_type`, `ref_id`, scope, direction, score 배열로 전달한다. 후보별 결과는 장기/단기 및 positive/negative 유형 점수와 match count만 가지며 edge path와 문구는 만들지 않는다. OTT는 최신 `movie_otts`를 두 번째 bounded query로 조회한다.

단기 후보 역조회는 행동마다 반복하지 않는다. 공통 행동 helper는 최근 24시간 positive를 영화별 최대 가중치로 누적하고 V3 worker가 `distinct>=3` 또는 `distinct>=2 and weight>=2.0` 기준, 30초 debounce, 2분 최대 대기를 평가한다. due user만 processing lease로 선점해 계산한다. API 코드는 V3 job을 import하지 않는다.

`user:{id}:v3:short_term_candidates`에는 최대 100개만 저장하고 ontology build, user, cache format으로 검증한다. 저장 TTL은 6시간에 사용자 ID 기반 결정적 0~30분 jitter를 더한다. 고정 벽시계 bucket을 signature에 넣지 않으므로 bucket 경계의 동시 cache miss가 없다. 최신 profile은 요청마다 다시 만들기 때문에 기준 미달 행동도 기존 후보 재정렬에는 즉시 반영된다. watched/passed는 cached 후보에서 즉시 제거한다. cache miss/손상/Redis 장애에서는 online service가 동일 set-based DB query로 복구하며 최종 추천이나 ontology/policy 결과는 cache하지 않는다.

Short-term retrieval은 정확한 reverse-edge 집계를 사용한다. Build `22`의 넓은 장르 하나는 약 28.8만 edge여서 약 0.95초가 측정됐다. 운영 p95가 기준을 넘으면 graph edge를 임의로 자르지 않고 feature별 사전 top-N inverted artifact를 build 파이프라인에 추가한다.

## 12. 점수와 진단 저장

후보별로 최소 다음 값을 분리한다.

```text
candidate source/rank
model raw/normalized score
short-term raw/normalized score
ontology total/type scores
policy adjustment by policy ID
final score/rank
explanation reason codes
model/ontology/policy versions
```

같은 feature 이름이 model과 ontology evidence에 등장해도 causal link를 만들지 않는다.

## 13. 실패 처리

- artifact가 없거나 손상되면 V3를 정상으로 표시하지 않는다.
- 새 build 실패 시 직전 정상 artifact와 후보를 유지한다.
- transient DB 오류만 제한적으로 재시도한다.
- worker는 advisory lock으로 중복 실행을 막는다.
- `subscribed_only` 후보 부족을 전체 catalog fallback으로 숨기지 않는다.
- 사용자별 후보 부족이나 degraded feature를 run diagnostics에 기록한다.

## 14. 성능 원칙

- graph build는 stage별 elapsed, rows, rows/sec를 기록한다.
- 변경 없는 source hash는 full graph build를 생략한다.
- overview extraction은 증분 처리를 지원한다.
- 대량 node/edge는 staging table과 bulk insert/upsert를 사용한다.
- online profile/retrieval/analyzer는 반환 row 수와 p50/p95를 기록한다.
- source 병합 순위는 최대 150개를 저장하고, 저비용 hard filter를 통과한 최대 100개만 상세 분석한다.
- explanation path는 최종 응답 후보에만 생성한다.
