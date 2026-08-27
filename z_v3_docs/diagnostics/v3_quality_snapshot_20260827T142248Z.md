# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-08-27T14:22:48.817455+00:00`
- 시나리오: `post-model`
- 사용자: `12`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | drift | 장기 후보→장기 | 단기 후보→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 0.701 | 0.259 | 0.857 | 0.422 | 0.338 | 0.042 | 20.0 |
| post_model_drift | 0.737 | 0.333 | 0.848 | 0.397 | 0.165 | 0.000 | 20.0 |

## Catalog 품질 요약

| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |
| --- | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 0.083 | 0.242 | 0.008 | 0.000 | 4.022e+12 |
| post_model_drift | 0.017 | 0.083 | 0.000 | 0.000 | 7.063e+12 |

## Negative 취향 잔존

| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |
| --- | ---: | ---: |
| post_model_stable | 0.148 | 0.917 |
| post_model_drift | 0.247 | 0.942 |

## 단기 후보 단계별 생존

| 유형 | 원본 단기 | 병합 150 단기 | 병합 단기 전용 | 병합 중복 | eligibility 100 | 최종 20 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | 100.0 | 41.3 | 40.8 | 0.5 | 11.8 | 0.8 |
| post_model_drift | 100.0 | 42.8 | 42.7 | 0.2 | 14.0 | 0.0 |

## 후보 집중도와 장기 점수

- 장기: `240`칸 / 고유 `68`편
- 단기: `240`칸 / 고유 `146`편
- 최종: `240`칸 / 고유 `89`편
- 최종 상위 5: `60`칸 / 고유 `34`편
- 장기 raw score 절댓값: min `4.897e+10`, median `1.176e+12`, p95 `3.885e+12`, max `7.063e+12`

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| v3seed-train-025@pinlm.test | post_model_stable | action_crime_thriller → action_crime_thriller | 모험, 액션, 스릴러 | 액션, 스릴러, 범죄 | 0.642 | 0.375 | 0.383 | 0.000 |
| v3seed-train-026@pinlm.test | post_model_stable | romance_drama_comedy → romance_drama_comedy | 로맨스, 드라마, 코미디 | 코미디, 드라마, 로맨스 | 0.731 | 0.797 | 0.797 | 0.050 |
| v3seed-train-027@pinlm.test | post_model_stable | horror_mystery_thriller → horror_mystery_thriller | 드라마, 공포, 스릴러 | 공포, 스릴러, 미스터리 | 0.715 | 0.532 | 0.343 | 0.050 |
| v3seed-train-028@pinlm.test | post_model_stable | animation_family_adventure → animation_family_adventure | 가족, 모험, 애니메이션 | 모험, 가족, 애니메이션 | 0.678 | 0.100 | 0.100 | 0.050 |
| v3seed-train-029@pinlm.test | post_model_stable | scifi_fantasy_adventure → scifi_fantasy_adventure | 모험, 판타지, 액션 | 모험, SF, 판타지 | 0.710 | 0.164 | 0.168 | 0.000 |
| v3seed-train-030@pinlm.test | post_model_stable | documentary_history_war → documentary_history_war | 전쟁, 드라마, 역사 | 역사, 다큐멘터리, 전쟁 | 0.729 | 0.566 | 0.235 | 0.100 |
| v3seed-train-037@pinlm.test | post_model_drift | action_crime_thriller → romance_drama_comedy | 모험, 드라마, 액션 | 드라마, 로맨스, 코미디 | 0.781 | 0.345 | 0.466 | 0.000 |
| v3seed-train-038@pinlm.test | post_model_drift | romance_drama_comedy → horror_mystery_thriller | 드라마, 코미디, 스릴러 | 스릴러, 미스터리, 공포 | 0.757 | 0.670 | 0.166 | 0.000 |
| v3seed-train-039@pinlm.test | post_model_drift | horror_mystery_thriller → animation_family_adventure | 모험, 드라마, 스릴러 | 가족, 모험, 애니메이션 | 0.777 | 0.441 | 0.050 | 0.000 |
| v3seed-train-058@pinlm.test | post_model_drift | animation_family_adventure → horror_mystery_thriller | 모험, 판타지, 액션 | 공포, 스릴러, 미스터리 | 0.749 | 0.146 | 0.229 | 0.000 |
| v3seed-train-035@pinlm.test | post_model_drift | scifi_fantasy_adventure → documentary_history_war | 모험, 액션, SF | 역사, 다큐멘터리, 전쟁 | 0.674 | 0.337 | 0.042 | 0.000 |
| v3seed-train-036@pinlm.test | post_model_drift | documentary_history_war → scifi_fantasy_adventure | 전쟁, 모험, 드라마 | 모험, 판타지, SF | 0.684 | 0.445 | 0.037 | 0.000 |

## 최종 추천 표본

- `v3seed-train-025@pinlm.test`: 미션 임파서블 (TMDB 954), 갱스 오브 뉴욕 (TMDB 3131), Max Headroom: 20 Minutes into the Future (TMDB 35933), 마피아 (TMDB 9835), 퍼시픽 림 (TMDB 68726)
- `v3seed-train-026@pinlm.test`: 타이타닉 (TMDB 597), 빅쇼트 (TMDB 318846), 원더러스트 (TMDB 50647), 기생충 (TMDB 496243), 플로렌스 (TMDB 315664)
- `v3seed-train-027@pinlm.test`: 피아니스트 (TMDB 423), 갱스 오브 뉴욕 (TMDB 3131), 연가시 (TMDB 121491), 양들의 침묵 (TMDB 274), Vault of Horror I (TMDB 473544)
- `v3seed-train-028@pinlm.test`: 라라랜드 (TMDB 313369), 아메리칸 메이드 (TMDB 337170), Kuningas kulkureitten (TMDB 505755), 빅쇼트 (TMDB 318846), 런 로니 런 (TMDB 14923)
- `v3seed-train-029@pinlm.test`: 맨 인 블랙 (TMDB 607), 샤이닝 (TMDB 694), 매트릭스 2: 리로디드 (TMDB 604), 머더 1600 (TMDB 9415), 컨택트 (TMDB 329865)
- `v3seed-train-030@pinlm.test`: 피아니스트 (TMDB 423), 히든 피겨스 (TMDB 381284), 갱스 오브 뉴욕 (TMDB 3131), 얼라이드 (TMDB 369885), 그린 북 (TMDB 490132)
- `v3seed-train-037@pinlm.test`: 미션 임파서블 (TMDB 954), Max Headroom: 20 Minutes into the Future (TMDB 35933), 그린 북 (TMDB 490132), 갱스 오브 뉴욕 (TMDB 3131), 퍼시픽 림 (TMDB 68726)
- `v3seed-train-038@pinlm.test`: 빅쇼트 (TMDB 318846), 기생충 (TMDB 496243), 행오버 (TMDB 18785), 컨택트 (TMDB 329865), 플로렌스 (TMDB 315664)
- `v3seed-train-039@pinlm.test`: 프리즈너스 (TMDB 146233), 기생충 (TMDB 496243), 라라랜드 (TMDB 313369), 샤이닝 (TMDB 694), 빅쇼트 (TMDB 318846)
- `v3seed-train-058@pinlm.test`: 식스 센스 (TMDB 745), 토이 스토리 2 (TMDB 863), 퍼스트 어벤져 (TMDB 1771), 라라랜드 (TMDB 313369), 샤이닝 (TMDB 694)
- `v3seed-train-035@pinlm.test`: 맨 인 블랙 (TMDB 607), 슈퍼배드 (TMDB 20352), 찰리와 초콜릿 공장 (TMDB 118), 매트릭스 2: 리로디드 (TMDB 604), 컨택트 (TMDB 329865)
- `v3seed-train-036@pinlm.test`: 피아니스트 (TMDB 423), 히든 피겨스 (TMDB 381284), 조조 래빗 (TMDB 515001), 그린 북 (TMDB 490132), 양들의 침묵 (TMDB 274)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
