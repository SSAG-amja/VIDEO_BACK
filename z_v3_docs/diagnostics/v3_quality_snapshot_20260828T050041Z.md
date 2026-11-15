# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-08-28T05:00:41.370934+00:00`
- 시나리오: `post-model`
- 사용자: `12`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | 상태 분포 | 최근 근거 | 의미 거리 | drift | model→장기 | 장기 ontology→장기 | 단기→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | {'stable': 6} | 1.000 | 0.572 | 0.341 | 0.360 | 0.551 | 0.858 | 0.680 | 0.634 | 0.400 | 20.0 |
| post_model_drift | {'drift': 6} | 1.000 | 0.882 | 0.819 | 0.416 | 0.505 | 0.848 | 0.522 | 0.580 | 0.650 | 20.0 |

## Catalog 품질 요약

| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |
| --- | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 0.033 | 0.075 | 0.000 | 0.000 | 2.484e-01 |
| post_model_drift | 0.025 | 0.125 | 0.000 | 0.025 | 6.726e-01 |

## Negative 취향 잔존

| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |
| --- | ---: | ---: |
| post_model_stable | 0.130 | 1.000 |
| post_model_drift | 0.352 | 1.000 |

## 단기 후보 단계별 생존

| 유형 | 원본 단기 | 병합 단기 전용 | 정책 통과 단기 전용 | lane 목표 | 정책 선택 단기 전용 | 강제 선택 | 최종 20 단기 전용 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 100.0 | 4.2 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| post_model_drift | 100.0 | 43.3 | 34.0 | 32.7 | 34.0 | 28.0 | 7.8 |

## 후보 집중도와 장기 점수

- 장기: `240`칸 / 고유 `45`편
- 단기: `240`칸 / 고유 `146`편
- 최종: `240`칸 / 고유 `160`편
- 최종 상위 5: `60`칸 / 고유 `42`편
- 장기 raw score 절댓값: min `7.515e-04`, median `5.640e-02`, p95 `5.883e-01`, max `6.726e-01`

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | 상태 | 근거 | 의미 거리 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v3seed-train-025@pinlm.test | post_model_stable | action_crime_thriller → action_crime_thriller | 모험, 액션, 스릴러 | 액션, 스릴러, 범죄 | stable | 1.000 | 0.549 | 0.306 | 0.653 | 0.613 | 0.400 |
| v3seed-train-026@pinlm.test | post_model_stable | romance_drama_comedy → romance_drama_comedy | 로맨스, 드라마, 코미디 | 코미디, 드라마, 로맨스 | stable | 1.000 | 0.559 | 0.322 | 0.867 | 0.867 | 0.700 |
| v3seed-train-027@pinlm.test | post_model_stable | horror_mystery_thriller → horror_mystery_thriller | 공포, 스릴러, 미스터리 | 공포, 스릴러, 미스터리 | stable | 1.000 | 0.620 | 0.416 | 0.706 | 0.706 | 0.650 |
| v3seed-train-028@pinlm.test | post_model_stable | animation_family_adventure → animation_family_adventure | 가족, 모험, 애니메이션 | 모험, 가족, 애니메이션 | stable | 1.000 | 0.684 | 0.514 | 0.463 | 0.463 | 0.250 |
| v3seed-train-029@pinlm.test | post_model_stable | scifi_fantasy_adventure → scifi_fantasy_adventure | 모험, 판타지, 액션 | 모험, SF, 판타지 | stable | 1.000 | 0.491 | 0.217 | 0.700 | 0.650 | 0.050 |
| v3seed-train-030@pinlm.test | post_model_stable | documentary_history_war → documentary_history_war | 전쟁, 드라마, 역사 | 역사, 다큐멘터리, 전쟁 | stable | 1.000 | 0.526 | 0.271 | 0.689 | 0.507 | 0.350 |
| v3seed-train-037@pinlm.test | post_model_drift | action_crime_thriller → romance_drama_comedy | 드라마, 액션, 코미디 | 드라마, 로맨스, 코미디 | drift | 1.000 | 0.903 | 0.850 | 0.550 | 0.667 | 0.600 |
| v3seed-train-038@pinlm.test | post_model_drift | romance_drama_comedy → horror_mystery_thriller | 드라마, 스릴러, 미스터리 | 스릴러, 미스터리, 공포 | drift | 1.000 | 0.809 | 0.706 | 0.621 | 0.812 | 1.000 |
| v3seed-train-039@pinlm.test | post_model_drift | horror_mystery_thriller → animation_family_adventure | 스릴러, 모험, 드라마 | 가족, 모험, 애니메이션 | drift | 1.000 | 0.789 | 0.676 | 0.391 | 0.389 | 0.450 |
| v3seed-train-058@pinlm.test | post_model_drift | animation_family_adventure → horror_mystery_thriller | 모험, 공포, 스릴러 | 공포, 스릴러, 미스터리 | drift | 1.000 | 0.925 | 0.885 | 0.571 | 0.774 | 0.750 |
| v3seed-train-035@pinlm.test | post_model_drift | scifi_fantasy_adventure → documentary_history_war | 모험, 액션, SF | 역사, 다큐멘터리, 전쟁 | drift | 1.000 | 0.944 | 0.914 | 0.623 | 0.275 | 0.400 |
| v3seed-train-036@pinlm.test | post_model_drift | documentary_history_war → scifi_fantasy_adventure | 드라마, 역사, 모험 | 모험, 판타지, SF | drift | 1.000 | 0.924 | 0.884 | 0.379 | 0.561 | 0.700 |

## 최종 추천 표본

- `v3seed-train-025@pinlm.test`: 어벤져스: 인피니티 워 (TMDB 299536), 어벤져스 (TMDB 24428), 다크 나이트 (TMDB 155), 킬 빌: 2부 (TMDB 393), 블랙 팬서 (TMDB 284054)
- `v3seed-train-026@pinlm.test`: 크레이지 스투피드 러브 (TMDB 50646), 실버라이닝 플레이북 (TMDB 82693), 러브, 로지 (TMDB 200727), 포레스트 검프 (TMDB 13), 크로스로드 (TMDB 17130)
- `v3seed-train-027@pinlm.test`: 겟 아웃 (TMDB 419430), 세븐 (TMDB 807), 호스맨 (TMDB 18476), 페일 블루 아이 (TMDB 800815), City of Blood (TMDB 85206)
- `v3seed-train-028@pinlm.test`: 니모를 찾아서 (TMDB 12), 어벤져스 (TMDB 24428), 어벤져스: 인피니티 워 (TMDB 299536), 토르: 다크 월드 (TMDB 76338), 빅 히어로 (TMDB 177572)
- `v3seed-train-029@pinlm.test`: 어벤져스: 인피니티 워 (TMDB 299536), 어벤져스 (TMDB 24428), 블랙 팬서 (TMDB 284054), 캡틴 아메리카: 시빌 워 (TMDB 271110), 캡틴 아메리카: 윈터 솔져 (TMDB 100402)
- `v3seed-train-030@pinlm.test`: 라이언 일병 구하기 (TMDB 857), 이미테이션 게임 (TMDB 205596), 쉰들러 리스트 (TMDB 424), 포그 오브 워 (TMDB 12698), 핵소 고지 (TMDB 324786)
- `v3seed-train-037@pinlm.test`: 어벤져스 (TMDB 24428), 크레이지 스투피드 러브 (TMDB 50646), 어벤져스: 인피니티 워 (TMDB 299536), 포레스트 검프 (TMDB 13), 주노 (TMDB 7326)
- `v3seed-train-038@pinlm.test`: 겟 아웃 (TMDB 419430), 싸이코 2 (TMDB 10576), 호스맨 (TMDB 18476), 살인소설 (TMDB 82507), 리그레션 (TMDB 241257)
- `v3seed-train-039@pinlm.test`: 프리즈너스 (TMDB 146233), 도리를 찾아서 (TMDB 127380), 세븐 (TMDB 807), 니모를 찾아서 (TMDB 12), 판타스틱 Mr. 폭스 (TMDB 10315)
- `v3seed-train-058@pinlm.test`: 겟 아웃 (TMDB 419430), 유전 (TMDB 493922), 딜런 독 : 죽음의 밤 (TMDB 43935), 컨저링 3: 악마가 시켰다 (TMDB 423108), 디 아더스 (TMDB 1933)
- `v3seed-train-035@pinlm.test`: 어벤져스 (TMDB 24428), Hitler et Churchill : le combat de l'aigle et du lion (TMDB 512840), 어벤져스: 인피니티 워 (TMDB 299536), 48 NOIDED MOVIES (TMDB 1529610), 캡틴 아메리카: 시빌 워 (TMDB 271110)
- `v3seed-train-036@pinlm.test`: 지구의 중심에서 (TMDB 26398), 뱀파이어 헌터 D (TMDB 15999), 쉰들러 리스트 (TMDB 424), 카오스 워킹 (TMDB 412656), 타임 머신 (TMDB 2134)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
