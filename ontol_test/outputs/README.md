# MovieLens Ontology Evaluation Output

이 디렉토리는 `ontol_test.evaluate_movielens` 실행 결과를 저장한다.
반복 실행하면 같은 파일이 덮어써진다.

## 파일 목록

### `evaluation_results.jsonl`

평가 대상이 된 유저별 결과 파일이다.
한 줄이 유저 한 명의 결과이며, 각 줄은 JSON 객체다.

필드:

- `user_id`: MovieLens의 유저 ID.
- `used_movie_count`: 해당 유저의 전체 후기 중 현재 DB 영화와 매칭되어 실제 평가에 사용된 영화 수.
- `total_count`: 해당 유저가 `ratings.csv`에 남긴 전체 후기 수.
- `candidate`: 온톨로지 추천 로직이 holdout 영화 전체를 점수화한 랭킹 목록.
- `summary`: 해당 유저 평가에 대한 보조 통계.

`candidate` 형식:

```json
[[326, 2.5], [1642, 1.5], [6637, 3]]
```

- 첫 번째 값: `tmdb_id`.
- 두 번째 값: `ratings.csv`의 원본 평점.
- 순서: 추천 랭킹 순서.
- 후보 수를 100개로 자르지 않고 holdout 전체를 기록한다.

`summary` 필드:

- `candidate_count`: `candidate`에 기록된 영화 수.
- `rated_candidate_count`: 후보군에 포함된 영화 중 원본 평점이 확인된 영화 수. 이 테스트에서는 holdout만 reranking하므로 보통 `candidate_count`와 같다.
- `holdout_ranked_count`: 학습 데이터에서 제외한 holdout 영화 중 온톨로지 로직으로 랭킹된 영화 수.
- `missing_count`: `ratings.csv`에는 있지만 현재 DB 영화와 매칭되지 않아 평가에서 제외된 영화 수.
- `train_count`: 행동 데이터로 사용한 영화 수.
- `holdout_count`: 정답 검증용으로 남겨둔 영화 수.

예시:

```json
{
  "user_id": 10,
  "used_movie_count": 656,
  "total_count": 660,
  "candidate": [[326, 2.5], [1642, 1.5]],
  "summary": {
    "candidate_count": 197,
    "holdout_ranked_count": 197,
    "missing_count": 4,
    "train_count": 459,
    "holdout_count": 197
  }
}
```

### `skipped_users.jsonl`

평가에서 제외된 유저 목록이다.
한 줄이 유저 한 명의 제외 사유이며, 각 줄은 JSON 객체다.

필드:

- `user_id`: MovieLens의 유저 ID.
- `rating_count`: 해당 유저의 전체 후기 수.
- `mapped_count`: 현재 DB 영화와 매칭된 후기 수. 매칭 수 부족으로 제외된 경우에만 포함된다.
- `missing_count`: 현재 DB 영화와 매칭되지 않은 후기 수. 매칭 수 부족으로 제외된 경우에만 포함된다.
- `reason`: 제외 사유.

`reason` 값:

- `ratings_below_minimum`: 전체 후기 수가 `min_ratings`보다 적음.
- `mapped_ratings_below_minimum`: DB에 매칭된 후기 수가 `min_mapped_ratings`보다 적음.

### `evaluation_run_summary.json`

한 번의 평가 실행 전체 요약 파일이다.

필드:

- `total_user_count`: 이번 실행에서 스캔한 전체 유저 수.
- `evaluated_user_count`: 실제 평가를 수행한 유저 수.
- `skipped_user_count`: 조건 미달로 제외된 유저 수.
- `total_rating_count`: 스캔한 유저들의 전체 후기 수 합계.
- `total_used_movie_count`: DB 영화와 매칭되어 실제 평가에 사용된 영화 수 합계.
- `total_missing_count`: DB 영화와 매칭되지 않은 영화 수 합계.
- `total_candidate_count`: 전체 유저의 `candidate` 수 합계.
- `total_rated_candidate_count`: 후보군 중 원본 평점이 확인된 영화 수 합계.
- `min_ratings`: 평가 대상 유저가 되기 위한 최소 전체 후기 수.
- `min_mapped_ratings`: 평가 대상 유저가 되기 위한 최소 DB 매칭 후기 수.
- `train_ratio`: 행동 데이터로 사용할 비율. 기본값은 `0.7`.
- `candidate_scope`: 후보 기록 범위. 현재는 `all_holdout`이다.
- `limit_arg`: 실행 시 전달된 `--limit` 값. 현재 MovieLens holdout 평가 출력에서는 후보 수 제한에 사용하지 않는다.
- `user_filters`: 실행 시 적용한 유저 필터.
- `rating_policy`: 원본 평점을 행동 데이터로 변환하는 기준.
- `elapsed_seconds`: 전체 실행 시간.
- `results_path`: 결과 파일 경로.
- `skipped_path`: 제외 유저 파일 경로.

## 평가 흐름

1. `ratings.csv`의 유저별 평점을 읽는다.
2. `links.csv`의 `tmdb_id`를 기준으로 현재 DB 영화와 매칭한다.
3. 매칭되지 않은 영화는 평가에서 제외하고 `missing_count`에 반영한다.
4. 매칭된 영화를 `ratings.csv`의 `timestamp` 오름차순으로 정렬한다.
5. 오래된 70%를 행동 데이터로 사용한다.
6. 최근 30%를 holdout 후보 풀로 둔다.
7. 온톨로지 추천 로직은 전체 DB가 아니라 holdout 후보 풀만 점수화한다.
8. 점수 순으로 holdout 전체를 `candidate`에 기록한다.
