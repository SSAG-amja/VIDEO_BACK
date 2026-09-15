# 진행 상황 기록 — 실험 완료

## 결과
`claude/results/comparison_report.md`, `claude/results/comparison_metrics.json`에 최종 비교 결과 저장됨.

요약 (NDCG@10, 247명 평가):
- Rule-based (실제 rec_pipeline.py 그대로 실행): 0.0303
- Popularity-only 베이스라인: 0.1706
- Random 베이스라인: 0.0104
- LightFM (WARP, 5-seed 평균±표준편차): 0.2043 ± 0.0031
- Wilcoxon signed-rank (Rule-based vs LightFM): p<0.0001, 유의미한 차이

**핵심 진단(버그 아님, 검증 완료)**: Rule-based가 Popularity-only보다도 낮은 이유는
`_accumulate_feature_candidates`의 `feature_scores[feature_id] * weight`(GENRE=8/KEYWORD=5/
DIRECTOR=6/ACTOR=3)가 유저의 과거 긍정 상호작용 수에 비례해 상한 없이 누적되기 때문. 상호작용이
많은 유저(이 실험 평균 ~114건)일수록 콘텐츠 매치 점수가 popularity 항(5~15 수준)을 수백~수천 배
압도해, 좁은 특징 공유 영화로만 추천이 쏠리고 미래 실제 선호와 거의 겹치지 않음(user_id=1 사례:
top-50 100% content 소스, 정답 적중 0건, LightFM은 같은 유저에서 5건 적중). 상세 근거는
comparison_report.md의 "핵심 진단" 절 참고.

## 전체 파이프라인 (재현용)
1. `app/jobs/recsys/evaluation/data_prep.py` — MovieLens 필터/70:30 분할/상태 매핑
2. Docker `db`+`redis` 기동 → alembic 마이그레이션 → `app.seed_db`로 실 카탈로그(영화 117만 건) 시딩
3. `app/jobs/recsys/evaluation/seed_real_db.py` — MovieLens 248유저를 실 DB에 시딩 (teardown: `teardown_real_db.py`, `claude/data/seed_manifest.json`)
4. `app/jobs/recsys/evaluation/rule_based_eval.py` + `run_rule_based.py` — 프로덕션 `rec_pipeline.py` 실제 함수 재사용, content/explore만 매칭 유니버스로 SQL 범위 제한 → `claude/results/rule_based_candidates.json`
5. `app/jobs/recsys/evaluation/lightfm_eval.py` — `recsys-eval:latest`(python:3.11-slim + lightfm) 컨테이너에서 실행 → `claude/results/lightfm_candidates.json`
6. `app/jobs/recsys/evaluation/metrics.py` — Precision/Recall/NDCG/MAP, Coverage, bootstrap CI, Wilcoxon
7. `app/jobs/recsys/evaluation/run_comparison.py` — 최종 비교 → `claude/results/comparison_report.md`

## 환경 메모 (다음에 재실행 시)
- 로컬 Python 3.13에서 lightfm 컴파일 불가 (CPython 내부 구조 비호환) → `docker run --network video_back_default -v <repo>:/work -e DB_HOST=db -e DB_PORT=5432 -w /work recsys-eval:latest python -m app.jobs.recsys.evaluation.lightfm_eval` 로 실행
- 로컬에서 DB 접속: `PYTHONPATH=<repo> DB_HOST=localhost DB_PORT=5442 python3 -m app.jobs.recsys.evaluation.<module>`
- `.claude/settings.local.json`에 안전한 자동 진행 권한 설정 완료 (git push/reset --hard/rm -rf/docker volume 삭제/.env 수정은 deny로 하드 차단)
- 시딩 되돌리기: `PYTHONPATH=<repo> DB_HOST=localhost DB_PORT=5442 python3 -m app.jobs.recsys.evaluation.teardown_real_db`

## 다음 논의 거리 (실행은 안 함, 사용자 판단 필요)
- rule-based 콘텐츠 가중치를 정규화/클리핑하면 popularity 대비 우위가 회복되는지 (별도 실험 가능)
- Phase 2(§6): 실 서비스에 LightFM 연결 여부는 이번 결과를 근거로 사용자가 결정
