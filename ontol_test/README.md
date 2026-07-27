# Ontology Recommendation Test

## 1. 테스트 요약

MovieLens 평점 데이터를 사용해서 온톨로지 기반 추천 랭킹 품질을 확인하는 테스트다.

테스트 흐름:

1. `ratings.csv`에서 유저별 평점 데이터를 읽는다.
2. `links.csv`의 `tmdb_id`를 기준으로 현재 DB의 영화와 매칭한다.
3. 매칭 가능한 영화만 평가에 사용하고, 매칭되지 않는 영화는 제외한다.
4. 유저별 평점을 `timestamp` 오름차순으로 정렬한다.
5. 오래된 70%를 행동 데이터로 보고 온톨로지 추천 프로필을 만든다.
6. 최근 30%를 holdout 후보 풀로 둔다.
7. 전체 DB에서 후보를 뽑지 않고, holdout 영화 전체만 온톨로지 점수로 랭킹한다.
8. 랭킹 결과와 원본 평점을 `ontol_test/outputs`에 저장한다.

평점 변환 기준:

```text
0.5~1.5: pass
2.0~3.0: watched/neutral
3.5~4.5: pin
5.0: saved/favorite
```

## 2. 실행하기 전 세팅

`.env`에서 추천 엔진이 v2로 설정되어 있어야 한다.

```env
RECOMMENDATION_ENGINE=v2
```

`ontol_test/inputs` 아래에 MovieLens CSV 3개가 있어야 한다.

```text
ontol_test/inputs/movies.csv
ontol_test/inputs/links.csv
ontol_test/inputs/ratings.csv
```

백엔드 컨테이너가 실행 중이어야 한다.

```bash
docker compose ps
```

필요한 DB 상태:

- 영화 데이터가 DB에 들어 있어야 한다.
- 온톨로지 그래프 build가 완료되어 있어야 한다.
- active ontology build가 설정되어 있어야 한다.

## 3. 실행 방법

기본 실행:

```bash
docker compose exec -T back-api python -m ontol_test.evaluate_movielens \
  --min-ratings 100 \
  --min-mapped-ratings 30 \
  --train-ratio 0.7
```

특정 유저만 실행:

```bash
docker compose exec -T back-api python -m ontol_test.evaluate_movielens \
  --min-ratings 100 \
  --min-mapped-ratings 30 \
  --train-ratio 0.7 \
  --user-ids 3968,31654,52909 \
  --max-users 3 \
  --progress-every 1
```

후기 100개 이상 유저 중 일부만 빠르게 확인할 때는 `--max-users`를 사용한다.

```bash
docker compose exec -T back-api python -m ontol_test.evaluate_movielens \
  --min-ratings 100 \
  --min-mapped-ratings 30 \
  --train-ratio 0.7 \
  --max-users 10 \
  --progress-every 1
```

출력은 기본적으로 `ontol_test/outputs`에 저장된다.
반복 실행하면 같은 파일을 덮어쓴다.

## 4. 출력 확인 방법

실행 후 아래 파일을 확인한다.

```text
ontol_test/outputs/evaluation_results.jsonl
ontol_test/outputs/skipped_users.jsonl
ontol_test/outputs/evaluation_run_summary.json
```

출력 파일과 필드의 자세한 설명은 `ontol_test/outputs/README.md`를 참고한다.
