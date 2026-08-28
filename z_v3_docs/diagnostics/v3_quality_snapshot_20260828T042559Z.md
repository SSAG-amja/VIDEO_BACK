# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-08-28T04:25:59.524492+00:00`
- 시나리오: `post-model`
- 사용자: `12`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | 상태 분포 | 최근 근거 | 의미 거리 | drift | 장기 후보→장기 | 단기 후보→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | {'stable': 6} | 1.000 | 0.582 | 0.357 | 0.371 | 0.858 | 0.564 | 0.431 | 0.033 | 20.0 |
| post_model_drift | {'drift': 6} | 1.000 | 0.876 | 0.810 | 0.463 | 0.848 | 0.476 | 0.382 | 0.383 | 20.0 |

## Catalog 품질 요약

| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |
| --- | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 0.000 | 0.000 | 0.000 | 0.000 | 2.992e-01 |
| post_model_drift | 0.008 | 0.050 | 0.000 | 0.008 | 2.797e-01 |

## Negative 취향 잔존

| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |
| --- | ---: | ---: |
| post_model_stable | 0.119 | 1.000 |
| post_model_drift | 0.312 | 1.000 |

## 단기 후보 단계별 생존

| 유형 | 원본 단기 | 병합 단기 전용 | 정책 통과 단기 전용 | lane 목표 | 정책 선택 단기 전용 | 강제 선택 | 최종 20 단기 전용 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 100.0 | 21.7 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| post_model_drift | 100.0 | 44.5 | 17.3 | 17.3 | 17.3 | 17.3 | 6.8 |

## 후보 집중도와 장기 점수

- 장기: `240`칸 / 고유 `45`편
- 단기: `240`칸 / 고유 `146`편
- 최종: `240`칸 / 고유 `108`편
- 최종 상위 5: `60`칸 / 고유 `34`편
- 장기 raw score 절댓값: min `3.445e-04`, median `9.923e-02`, p95 `2.186e-01`, max `2.992e-01`

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | 상태 | 근거 | 의미 거리 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v3seed-train-025@pinlm.test | post_model_stable | action_crime_thriller → action_crime_thriller | 모험, 액션, 스릴러 | 액션, 스릴러, 범죄 | stable | 1.000 | 0.532 | 0.280 | 0.633 | 0.396 | 0.000 |
| v3seed-train-026@pinlm.test | post_model_stable | romance_drama_comedy → romance_drama_comedy | 로맨스, 드라마, 코미디 | 코미디, 드라마, 로맨스 | stable | 1.000 | 0.643 | 0.451 | 0.399 | 0.399 | 0.050 |
| v3seed-train-027@pinlm.test | post_model_stable | horror_mystery_thriller → horror_mystery_thriller | 드라마, 공포, 스릴러 | 공포, 스릴러, 미스터리 | stable | 1.000 | 0.649 | 0.459 | 0.588 | 0.475 | 0.050 |
| v3seed-train-028@pinlm.test | post_model_stable | animation_family_adventure → animation_family_adventure | 가족, 모험, 애니메이션 | 모험, 가족, 애니메이션 | stable | 1.000 | 0.641 | 0.447 | 0.476 | 0.476 | 0.100 |
| v3seed-train-029@pinlm.test | post_model_stable | scifi_fantasy_adventure → scifi_fantasy_adventure | 모험, 판타지, 액션 | 모험, SF, 판타지 | stable | 1.000 | 0.478 | 0.197 | 0.598 | 0.510 | 0.000 |
| v3seed-train-030@pinlm.test | post_model_stable | documentary_history_war → documentary_history_war | 전쟁, 드라마, 역사 | 역사, 다큐멘터리, 전쟁 | stable | 1.000 | 0.548 | 0.305 | 0.691 | 0.331 | 0.000 |
| v3seed-train-037@pinlm.test | post_model_drift | action_crime_thriller → romance_drama_comedy | 모험, 드라마, 액션 | 드라마, 로맨스, 코미디 | drift | 1.000 | 0.886 | 0.825 | 0.504 | 0.446 | 0.400 |
| v3seed-train-038@pinlm.test | post_model_drift | romance_drama_comedy → horror_mystery_thriller | 드라마, 코미디, 스릴러 | 스릴러, 미스터리, 공포 | drift | 1.000 | 0.815 | 0.715 | 0.482 | 0.509 | 0.400 |
| v3seed-train-039@pinlm.test | post_model_drift | horror_mystery_thriller → animation_family_adventure | 모험, 드라마, 스릴러 | 가족, 모험, 애니메이션 | drift | 1.000 | 0.784 | 0.668 | 0.462 | 0.347 | 0.400 |
| v3seed-train-058@pinlm.test | post_model_drift | animation_family_adventure → horror_mystery_thriller | 모험, 판타지, 액션 | 공포, 스릴러, 미스터리 | drift | 1.000 | 0.922 | 0.879 | 0.378 | 0.405 | 0.400 |
| v3seed-train-035@pinlm.test | post_model_drift | scifi_fantasy_adventure → documentary_history_war | 모험, 액션, SF | 역사, 다큐멘터리, 전쟁 | drift | 1.000 | 0.934 | 0.898 | 0.529 | 0.268 | 0.350 |
| v3seed-train-036@pinlm.test | post_model_drift | documentary_history_war → scifi_fantasy_adventure | 전쟁, 모험, 드라마 | 모험, 판타지, SF | drift | 1.000 | 0.918 | 0.873 | 0.503 | 0.318 | 0.350 |

## 최종 추천 표본

- `v3seed-train-025@pinlm.test`: 어벤져스: 인피니티 워 (TMDB 299536), 어벤져스 (TMDB 24428), 존 윅 (TMDB 245891), 가디언즈 오브 갤럭시 (TMDB 118340), 펄프 픽션 (TMDB 680)
- `v3seed-train-026@pinlm.test`: 포레스트 검프 (TMDB 13), 타이타닉 (TMDB 597), 인사이드 아웃 (TMDB 150540), 데드풀 (TMDB 293660), 펄프 픽션 (TMDB 680)
- `v3seed-train-027@pinlm.test`: 겟 아웃 (TMDB 419430), 셔터 아일랜드 (TMDB 11324), 23 아이덴티티 (TMDB 381288), 파이트 클럽 (TMDB 550), 바스터즈: 거친 녀석들 (TMDB 16869)
- `v3seed-train-028@pinlm.test`: 니모를 찾아서 (TMDB 12), 어벤져스 (TMDB 24428), 겨울왕국 (TMDB 109445), 인사이드 아웃 (TMDB 150540), 라따뚜이 (TMDB 2062)
- `v3seed-train-029@pinlm.test`: 어벤져스: 인피니티 워 (TMDB 299536), 어벤져스 (TMDB 24428), 라따뚜이 (TMDB 2062), 데드풀 (TMDB 293660), 가디언즈 오브 갤럭시 (TMDB 118340)
- `v3seed-train-030@pinlm.test`: 핵소 고지 (TMDB 324786), 덩케르크 (TMDB 374720), 라이언 일병 구하기 (TMDB 857), 이미테이션 게임 (TMDB 205596), 피아니스트 (TMDB 423)
- `v3seed-train-037@pinlm.test`: 포레스트 검프 (TMDB 13), 크레이지 스투피드 러브 (TMDB 50646), 어벤져스 (TMDB 24428), 어벤져스: 인피니티 워 (TMDB 299536), 500일의 썸머 (TMDB 19913)
- `v3seed-train-038@pinlm.test`: 겟 아웃 (TMDB 419430), 유전 (TMDB 493922), 포레스트 검프 (TMDB 13), 셔터 아일랜드 (TMDB 11324), 호스맨 (TMDB 18476)
- `v3seed-train-039@pinlm.test`: 셔터 아일랜드 (TMDB 11324), 도리를 찾아서 (TMDB 127380), 펄프 픽션 (TMDB 680), 파이트 클럽 (TMDB 550), 라푼젤 (TMDB 38757)
- `v3seed-train-058@pinlm.test`: 겟 아웃 (TMDB 419430), 유전 (TMDB 493922), 어벤져스: 인피니티 워 (TMDB 299536), 해리 포터와 비밀의 방 (TMDB 672), 디 아더스 (TMDB 1933)
- `v3seed-train-035@pinlm.test`: 어벤져스 (TMDB 24428), 데이 쉘 낫 그로우 올드 (TMDB 543580), 어벤져스: 인피니티 워 (TMDB 299536), 데드풀 (TMDB 293660), 시대정신: 어덴덤 (TMDB 13180)
- `v3seed-train-036@pinlm.test`: 라이언 일병 구하기 (TMDB 857), 카오스 워킹 (TMDB 412656), 핵소 고지 (TMDB 324786), 피아니스트 (TMDB 423), 잃어버린 아이들의 도시 (TMDB 902)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
