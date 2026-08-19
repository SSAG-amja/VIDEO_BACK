# 고정 사용자 추천 평가

한 번 실행하면 `cohorts.json`에 정의된 10/50/100/150/200/500명 사용자군을 모두 평가한다.

```powershell
python -m evaluation "테스트 이름"
```

일반 평가 실행은 저장소에 포함된 `evaluation/data/fixed_cases.jsonl.gz`만 읽는다.
원본 `ml-32m.zip`은 고정 사용자 구성을 변경하거나 데이터셋을 재생성할 때만 사용한다.

```powershell
python -m evaluation.prepare_cases --dataset "..\ml-32m.zip"
```

전처리기는 ZIP 안의 안전 매핑된 `filtered_links.csv`, `filtered_ratings.csv`,
`test_ids.csv`를 사용한다. 추천 엔진에는 평가 평점을 제외한 과거 행동과 후보 영화
ID만 전달한다.

추천 엔진은 `evaluation.contracts.EvaluationEngine` 계약을 구현하고 애플리케이션의
다음 factory에서 반환한다.

```python
app.services.recsys.evaluation:get_evaluation_engine
```

V1/V2/V3처럼 구현 방식이 달라도 평가 모듈 내부에 알고리즘별 파일을 추가하지 않는다.
개발 중 임시 factory는 다음처럼 지정할 수 있다.

```powershell
python -m evaluation "테스트 이름" `
  --engine-factory "package.module:create_engine"
```

결과는 한 실행당 JSON 하나만 생성한다.

```text
evaluation_results/<테스트 이름>/<실행시각>.json
```

JSON에는 실행 시작·종료 시각, 엔진 이름·버전, 데이터 SHA-256, 사용자군별 사용자 ID,
사용자별 지표와 평균·중앙값·표준편차가 기록된다. 기존 결과는 덮어쓰지 않는다.
