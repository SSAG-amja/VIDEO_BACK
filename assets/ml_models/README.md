# ML Models

## LightFM (2026.07.28, 김광원)

추천 모델이 Rule-based에서 LightFM으로 교체됨 (`app/jobs/recsys/lightfm_pipeline.py`).

- 학습/추론 로직: `app/services/recsys/lightfm_model.py`
- 배치 오케스트레이션: `app/jobs/recsys/lightfm_pipeline.py` (야간 스케줄러가 호출)
- 매 배치마다 새로 학습하며 모델 파일을 이 디렉터리에 영속화하지 않음 (기존 rule-based 배치와
  동일하게 "매일 전체 재계산" 방식 유지). 향후 학습 비용이 커지면 이 디렉터리에
  `.pkl`/`.npz` 형태로 모델을 저장해 재사용하는 최적화를 고려할 수 있음.
- 콜드스타트(상호작용 이력 없는 신규 유저)는 LightFM이 다룰 수 없어 제외되며, 계속
  `app/services/recsys/dynamic_retriever.py`의 온보딩 로직이 담당함.
- 추천 결과를 어떻게 뽑는지는 `docs/lightfm_integration.md` 참고.

Current integration scope:

- Keep only the directory structure.
- Do not add model loading code yet.
- Do not commit large model binaries.
- Add model-specific documentation here when the ML-based recommender is implemented.
