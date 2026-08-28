# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-08-27T22:16:57.399878+00:00`
- 시나리오: `post-model`
- 사용자: `12`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | 상태 분포 | 최근 근거 | 의미 거리 | drift | 장기 후보→장기 | 단기 후보→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | {'stable': 6} | 1.000 | 0.582 | 0.356 | 0.259 | 0.857 | 0.409 | 0.303 | 0.000 | 20.0 |
| post_model_drift | {'drift': 6} | 1.000 | 0.876 | 0.810 | 0.333 | 0.848 | 0.345 | 0.398 | 0.342 | 20.0 |

## Catalog 품질 요약

| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |
| --- | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 0.117 | 0.300 | 0.008 | 0.000 | 4.022e+12 |
| post_model_drift | 0.017 | 0.050 | 0.000 | 0.008 | 7.063e+12 |

## Negative 취향 잔존

| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |
| --- | ---: | ---: |
| post_model_stable | 0.160 | 0.925 |
| post_model_drift | 0.318 | 0.967 |

## 단기 후보 단계별 생존

| 유형 | 원본 단기 | 병합 단기 전용 | 정책 통과 단기 전용 | lane 목표 | 정책 선택 단기 전용 | 강제 선택 | 최종 20 단기 전용 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 100.0 | 22.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| post_model_drift | 100.0 | 46.0 | 19.0 | 19.0 | 19.0 | 19.0 | 6.8 |

## 후보 집중도와 장기 점수

- 장기: `240`칸 / 고유 `68`편
- 단기: `240`칸 / 고유 `146`편
- 최종: `240`칸 / 고유 `114`편
- 최종 상위 5: `60`칸 / 고유 `42`편
- 장기 raw score 절댓값: min `4.897e+10`, median `1.176e+12`, p95 `3.885e+12`, max `7.063e+12`

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | 상태 | 근거 | 의미 거리 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v3seed-train-025@pinlm.test | post_model_stable | action_crime_thriller → action_crime_thriller | 모험, 액션, 스릴러 | 액션, 스릴러, 범죄 | stable | 1.000 | 0.532 | 0.280 | 0.375 | 0.383 | 0.000 |
| v3seed-train-026@pinlm.test | post_model_stable | romance_drama_comedy → romance_drama_comedy | 로맨스, 드라마, 코미디 | 코미디, 드라마, 로맨스 | stable | 1.000 | 0.643 | 0.451 | 0.772 | 0.772 | 0.000 |
| v3seed-train-027@pinlm.test | post_model_stable | horror_mystery_thriller → horror_mystery_thriller | 드라마, 공포, 스릴러 | 공포, 스릴러, 미스터리 | stable | 1.000 | 0.649 | 0.459 | 0.532 | 0.310 | 0.000 |
| v3seed-train-028@pinlm.test | post_model_stable | animation_family_adventure → animation_family_adventure | 가족, 모험, 애니메이션 | 모험, 가족, 애니메이션 | stable | 1.000 | 0.641 | 0.447 | 0.050 | 0.050 | 0.000 |
| v3seed-train-029@pinlm.test | post_model_stable | scifi_fantasy_adventure → scifi_fantasy_adventure | 모험, 판타지, 액션 | 모험, SF, 판타지 | stable | 1.000 | 0.478 | 0.196 | 0.189 | 0.168 | 0.000 |
| v3seed-train-030@pinlm.test | post_model_stable | documentary_history_war → documentary_history_war | 전쟁, 드라마, 역사 | 역사, 다큐멘터리, 전쟁 | stable | 1.000 | 0.548 | 0.304 | 0.532 | 0.135 | 0.000 |
| v3seed-train-037@pinlm.test | post_model_drift | action_crime_thriller → romance_drama_comedy | 모험, 드라마, 액션 | 드라마, 로맨스, 코미디 | drift | 1.000 | 0.886 | 0.825 | 0.370 | 0.624 | 0.350 |
| v3seed-train-038@pinlm.test | post_model_drift | romance_drama_comedy → horror_mystery_thriller | 드라마, 코미디, 스릴러 | 스릴러, 미스터리, 공포 | drift | 1.000 | 0.814 | 0.714 | 0.594 | 0.383 | 0.350 |
| v3seed-train-039@pinlm.test | post_model_drift | horror_mystery_thriller → animation_family_adventure | 모험, 드라마, 스릴러 | 가족, 모험, 애니메이션 | drift | 1.000 | 0.784 | 0.668 | 0.424 | 0.300 | 0.300 |
| v3seed-train-058@pinlm.test | post_model_drift | animation_family_adventure → horror_mystery_thriller | 모험, 판타지, 액션 | 공포, 스릴러, 미스터리 | drift | 1.000 | 0.922 | 0.879 | 0.062 | 0.517 | 0.350 |
| v3seed-train-035@pinlm.test | post_model_drift | scifi_fantasy_adventure → documentary_history_war | 모험, 액션, SF | 역사, 다큐멘터리, 전쟁 | drift | 1.000 | 0.934 | 0.898 | 0.216 | 0.268 | 0.350 |
| v3seed-train-036@pinlm.test | post_model_drift | documentary_history_war → scifi_fantasy_adventure | 전쟁, 모험, 드라마 | 모험, 판타지, SF | drift | 1.000 | 0.918 | 0.873 | 0.403 | 0.297 | 0.350 |

## 최종 추천 표본

- `v3seed-train-025@pinlm.test`: 갱스 오브 뉴욕 (TMDB 3131), 미션 임파서블 (TMDB 954), 마피아 (TMDB 9835), Max Headroom: 20 Minutes into the Future (TMDB 35933), 피아니스트 (TMDB 423)
- `v3seed-train-026@pinlm.test`: 타이타닉 (TMDB 597), 빅쇼트 (TMDB 318846), 원더러스트 (TMDB 50647), 기생충 (TMDB 496243), 플로렌스 (TMDB 315664)
- `v3seed-train-027@pinlm.test`: 피아니스트 (TMDB 423), 갱스 오브 뉴욕 (TMDB 3131), 양들의 침묵 (TMDB 274), 연가시 (TMDB 121491), Vault of Horror I (TMDB 473544)
- `v3seed-train-028@pinlm.test`: 라라랜드 (TMDB 313369), 아메리칸 메이드 (TMDB 337170), Kuningas kulkureitten (TMDB 505755), 빅쇼트 (TMDB 318846), 런 로니 런 (TMDB 14923)
- `v3seed-train-029@pinlm.test`: 맨 인 블랙 (TMDB 607), 샤이닝 (TMDB 694), 매트릭스 2: 리로디드 (TMDB 604), 머더 1600 (TMDB 9415), 컨택트 (TMDB 329865)
- `v3seed-train-030@pinlm.test`: 피아니스트 (TMDB 423), 갱스 오브 뉴욕 (TMDB 3131), 히든 피겨스 (TMDB 381284), 그린 북 (TMDB 490132), 얼라이드 (TMDB 369885)
- `v3seed-train-037@pinlm.test`: 미션 임파서블 (TMDB 954), 크레이지 스투피드 러브 (TMDB 50646), Max Headroom: 20 Minutes into the Future (TMDB 35933), 갱스 오브 뉴욕 (TMDB 3131), 라라랜드 (TMDB 313369)
- `v3seed-train-038@pinlm.test`: 빅쇼트 (TMDB 318846), 유전 (TMDB 493922), 기생충 (TMDB 496243), 행오버 (TMDB 18785), 겟 아웃 (TMDB 419430)
- `v3seed-train-039@pinlm.test`: 프리즈너스 (TMDB 146233), 니모를 찾아서 (TMDB 12), 기생충 (TMDB 496243), 라라랜드 (TMDB 313369), 정글북 (TMDB 9325)
- `v3seed-train-058@pinlm.test`: 식스 센스 (TMDB 745), 유전 (TMDB 493922), 토이 스토리 2 (TMDB 863), 퍼스트 어벤져 (TMDB 1771), 컨저링 3: 악마가 시켰다 (TMDB 423108)
- `v3seed-train-035@pinlm.test`: 맨 인 블랙 (TMDB 607), 데이 쉘 낫 그로우 올드 (TMDB 543580), 슈퍼배드 (TMDB 20352), 매트릭스 2: 리로디드 (TMDB 604), 포그 오브 워 (TMDB 12698)
- `v3seed-train-036@pinlm.test`: 피아니스트 (TMDB 423), 카오스 워킹 (TMDB 412656), 히든 피겨스 (TMDB 381284), 조조 래빗 (TMDB 515001), 잃어버린 아이들의 도시 (TMDB 902)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
