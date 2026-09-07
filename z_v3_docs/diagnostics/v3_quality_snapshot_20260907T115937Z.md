# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-09-07T11:59:37.804567+00:00`
- 시나리오: `representative`
- 사용자: `24`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | 상태 분포 | 최근 근거 | 의미 거리 | drift | model→장기 | 장기 ontology→장기 | 단기→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| stable | {'stable': 6} | 1.000 | 0.304 | 0.002 | 0.706 | 0.755 | 0.718 | 0.787 | 0.787 | 0.592 | 20.0 |
| mixed | {'stable': 5, 'inactive': 1} | 0.979 | 0.408 | 0.061 | 0.430 | 0.455 | 0.651 | 0.485 | 0.489 | 0.467 | 20.0 |
| drift | {'drift': 6} | 1.000 | 0.927 | 0.887 | 0.256 | 0.439 | 0.696 | 0.629 | 0.706 | 0.992 | 20.0 |
| negative_heavy | {'stable': 2, 'inactive': 4} | 0.333 | 0.083 | 0.000 | 0.725 | 0.783 | 0.295 | 0.798 | 0.321 | 0.242 | 20.0 |

## Catalog 품질 요약

| 유형 | vote 0 | vote < 20 | 장르 없음 | 장르 8개 이상 | 장기 raw score 최대 절댓값 |
| --- | ---: | ---: | ---: | ---: | ---: |
| stable | 0.000 | 0.017 | 0.000 | 0.008 | 9.073e-02 |
| mixed | 0.075 | 0.192 | 0.000 | 0.042 | 1.120e-01 |
| drift | 0.008 | 0.125 | 0.000 | 0.033 | 9.085e-02 |
| negative_heavy | 0.008 | 0.025 | 0.008 | 0.008 | 7.916e-02 |

## Negative 취향 잔존

| 유형 | 최종→negative 장르 | negative evidence가 있는 최종 후보 |
| --- | ---: | ---: |
| stable | 0.005 | 0.925 |
| mixed | 0.110 | 0.950 |
| drift | 0.039 | 1.000 |
| negative_heavy | 0.003 | 0.900 |

## 단기 후보 단계별 생존

| 유형 | 원본 단기 | 병합 단기 전용 | 정책 통과 단기 전용 | lane 목표 | 정책 선택 단기 전용 | 강제 선택 | 최종 20 단기 전용 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| stable | 100.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| mixed | 100.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| drift | 100.0 | 33.0 | 28.2 | 28.2 | 28.2 | 23.3 | 7.7 |
| negative_heavy | 33.3 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

## 후보 집중도와 장기 점수

- 장기: `480`칸 / 고유 `151`편
- 단기: `400`칸 / 고유 `317`편
- 최종: `480`칸 / 고유 `310`편
- 최종 상위 5: `120`칸 / 고유 `90`편
- 장기 raw score 절댓값: min `2.304e-02`, median `5.862e-02`, p95 `7.988e-02`, max `1.120e-01`

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | 상태 | 근거 | 의미 거리 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v3seed-train-007@pinlm.test | stable | action_crime_thriller | 액션, 스릴러, 범죄 | 액션, 스릴러, 범죄 | stable | 1.000 | 0.313 | 0.000 | 0.930 | 0.930 | 0.400 |
| v3seed-train-008@pinlm.test | stable | romance_drama_comedy | 로맨스, 드라마, 코미디 | 로맨스, 드라마, 코미디 | stable | 1.000 | 0.305 | 0.000 | 0.950 | 0.950 | 0.450 |
| v3seed-train-009@pinlm.test | stable | horror_mystery_thriller | 공포, 스릴러, 미스터리 | 공포, 스릴러, 미스터리 | stable | 1.000 | 0.231 | 0.000 | 0.890 | 0.890 | 0.550 |
| v3seed-train-010@pinlm.test | stable | animation_family_adventure | 가족, 모험, 애니메이션 | 가족, 모험, 애니메이션 | stable | 1.000 | 0.359 | 0.015 | 0.690 | 0.690 | 0.400 |
| v3seed-train-011@pinlm.test | stable | scifi_fantasy_adventure | 모험, 판타지, SF | 모험, 판타지, SF | stable | 1.000 | 0.319 | 0.000 | 0.509 | 0.509 | 0.900 |
| v3seed-train-012@pinlm.test | stable | documentary_history_war | 전쟁, 드라마, 역사 | 전쟁, 역사, 드라마 | stable | 1.000 | 0.295 | 0.000 | 0.752 | 0.752 | 0.850 |
| v3seed-train-073@pinlm.test | mixed | action_crime_thriller | 액션, 스릴러, 범죄 | 액션, 스릴러, 모험 | stable | 1.000 | 0.354 | 0.006 | 0.492 | 0.464 | 0.300 |
| v3seed-train-074@pinlm.test | mixed | romance_drama_comedy | 로맨스, 드라마, 액션 | 로맨스, 드라마, 코미디 | stable | 1.000 | 0.376 | 0.041 | 0.479 | 0.449 | 0.900 |
| v3seed-train-075@pinlm.test | mixed | horror_mystery_thriller | 공포, 액션, 스릴러 | 스릴러, 공포, 미스터리 | stable | 1.000 | 0.339 | 0.000 | 0.495 | 0.682 | 0.750 |
| v3seed-train-076@pinlm.test | mixed | animation_family_adventure | 가족, 모험, 판타지 | 모험, 애니메이션, 가족 | stable | 1.000 | 0.404 | 0.083 | 0.504 | 0.609 | 0.750 |
| v3seed-train-077@pinlm.test | mixed | scifi_fantasy_adventure | 가족, 모험, 판타지 | 모험, 판타지, 액션 | stable | 1.000 | 0.503 | 0.235 | 0.415 | 0.348 | 0.100 |
| v3seed-train-078@pinlm.test | mixed | documentary_history_war | 로맨스, 전쟁, 드라마 | 로맨스, 드라마, 코미디 | inactive | 0.875 | 0.475 | 0.000 | 0.528 | 0.383 | 0.000 |
| v3seed-train-097@pinlm.test | drift | action_crime_thriller | 로맨스, 드라마, 코미디 | 로맨스, 드라마, 코미디 | drift | 1.000 | 0.935 | 0.901 | 0.968 | 0.968 | 1.000 |
| v3seed-train-098@pinlm.test | drift | romance_drama_comedy | 드라마, 공포, 코미디 | 공포, 스릴러, 미스터리 | drift | 1.000 | 0.969 | 0.953 | 0.317 | 0.767 | 1.000 |
| v3seed-train-099@pinlm.test | drift | horror_mystery_thriller | 가족, 모험, 애니메이션 | 가족, 모험, 애니메이션 | drift | 1.000 | 0.964 | 0.945 | 0.647 | 0.647 | 1.000 |
| v3seed-train-100@pinlm.test | drift | animation_family_adventure | 공포, 스릴러, 미스터리 | 공포, 스릴러, 미스터리 | drift | 1.000 | 0.964 | 0.945 | 0.815 | 0.815 | 1.000 |
| v3seed-train-101@pinlm.test | drift | scifi_fantasy_adventure | 전쟁, 역사, 다큐멘터리 | 전쟁, 역사, 다큐멘터리 | drift | 1.000 | 0.875 | 0.808 | 0.530 | 0.530 | 0.950 |
| v3seed-train-102@pinlm.test | drift | documentary_history_war | 모험, 판타지, 액션 | 모험, 판타지, SF | drift | 1.000 | 0.853 | 0.774 | 0.498 | 0.506 | 1.000 |
| v3seed-train-109@pinlm.test | negative_heavy | action_crime_thriller | 액션, 스릴러, 범죄 | 액션, 스릴러, 범죄 | stable | 1.000 | 0.248 | 0.000 | 0.963 | 0.963 | 0.700 |
| v3seed-train-110@pinlm.test | negative_heavy | romance_drama_comedy | 로맨스, 드라마, 코미디 | 로맨스, 드라마, 코미디 | stable | 1.000 | 0.248 | 0.000 | 0.963 | 0.963 | 0.750 |
| v3seed-train-111@pinlm.test | negative_heavy | horror_mystery_thriller | 공포, 스릴러, 미스터리 | 없음 | inactive | 0.000 | 0.000 | 0.000 | 0.930 | 0.000 | 0.000 |
| v3seed-train-112@pinlm.test | negative_heavy | animation_family_adventure | 가족, 모험, 애니메이션 | 없음 | inactive | 0.000 | 0.000 | 0.000 | 0.685 | 0.000 | 0.000 |
| v3seed-train-113@pinlm.test | negative_heavy | scifi_fantasy_adventure | 모험, 판타지, SF | 없음 | inactive | 0.000 | 0.000 | 0.000 | 0.508 | 0.000 | 0.000 |
| v3seed-train-114@pinlm.test | negative_heavy | documentary_history_war | 전쟁, 역사, 드라마 | 없음 | inactive | 0.000 | 0.000 | 0.000 | 0.738 | 0.000 | 0.000 |

## 최종 추천 표본

- `v3seed-train-007@pinlm.test`: 분노의 질주: 라이드 오어 다이 (TMDB 385687), 분노의 질주: 더 익스트림 (TMDB 337339), 존 윅 3: 파라벨룸 (TMDB 458156), 리썰 웨폰 3 (TMDB 943), 트랜스포터: 엑스트림 (TMDB 9335)
- `v3seed-train-008@pinlm.test`: 카페 소사이어티 (TMDB 339397), 하이 스쿨 뮤지컬 (TMDB 10947), 네번의 결혼식과 한번의 장례식 (TMDB 712), 러브, 로지 (TMDB 200727), 헤어스프레이 (TMDB 2976)
- `v3seed-train-009@pinlm.test`: 더 보이 2: 돌아온 브람스 (TMDB 555974), 아이 씨 유 (TMDB 524251), 별장에서 생긴 일 (TMDB 474764), 엑스텐션 (TMDB 10226), 더 보이 (TMDB 321258)
- `v3seed-train-010@pinlm.test`: 토이 스토리 4 (TMDB 301528), 모아나 (TMDB 277834), 슈렉 3 (TMDB 810), 카 (TMDB 920), 마다가스카 3: 이번엔 서커스다! (TMDB 80321)
- `v3seed-train-011@pinlm.test`: 멍키본 (TMDB 23685), 원더우먼 (TMDB 15359), 미스터리 맨 (TMDB 9824), 저스티스 리그: 갓 앤 몬스터 (TMDB 323027), 아브릴과 조작된 세계 (TMDB 340357)
- `v3seed-train-012@pinlm.test`: 미드웨이 (TMDB 522162), Hiroshima (TMDB 68297), 미드웨이 (TMDB 11422), 패트리어트: 늪 속의 여우 (TMDB 2024), 씬 레드 라인 (TMDB 8741)
- `v3seed-train-073@pinlm.test`: 분노의 질주: 더 익스트림 (TMDB 337339), 드래곤볼 에볼루션 (TMDB 14164), 배트맨: 배드 블러드 (TMDB 366924), 배트맨: 배트우먼의 수수께끼 (TMDB 21683), 저스티스 리그: 크라이시스 온 투 어스 (TMDB 30061)
- `v3seed-train-074@pinlm.test`: 셋 잇 오프 (TMDB 9400), 밤의 미녀 (TMDB 11338), 필링 미네소타 (TMDB 12656), 머니 머니 (TMDB 12251), 위험한 트릭 (TMDB 109161)
- `v3seed-train-075@pinlm.test`: 킬 위드 미 (TMDB 8090), 페일 블루 아이 (TMDB 800815), 호스맨 (TMDB 18476), 큐어 (TMDB 36095), 뉴욕 리퍼 (TMDB 30874)
- `v3seed-train-076@pinlm.test`: 빅 히어로 (TMDB 177572), 드래곤 길들이기 (TMDB 10191), 헤라클레스 (TMDB 11970), 토이 스토리 4 (TMDB 301528), 카 (TMDB 920)
- `v3seed-train-077@pinlm.test`: 맥시멈 라이드 (TMDB 339116), 빅 히어로 (TMDB 177572), 로봇 치킨: 스타 워즈 에피소드 1 (TMDB 42979), 언더독 (TMDB 6589), 하워드 덕 (TMDB 10658)
- `v3seed-train-078@pinlm.test`: 직업 군인 캔디씨 이야기 (TMDB 25037), 라파예트 (TMDB 9664), 시칠리아 상륙작전 (TMDB 403450), 분노의 질주 (TMDB 9799), 라이언 일병 구하기 (TMDB 857)
- `v3seed-train-097@pinlm.test`: 브리짓 존스의 일기 (TMDB 634), 애니 홀 (TMDB 703), 티파니에서 아침을 (TMDB 164), 러브 앤 프렌즈 (TMDB 49022), 카페 소사이어티 (TMDB 339397)
- `v3seed-train-098@pinlm.test`: 케이스 39 (TMDB 28355), 페일 블루 아이 (TMDB 800815), 스노우맨 (TMDB 372343), 시스터스 (TMDB 22307), 킬 위드 미 (TMDB 8090)
- `v3seed-train-099@pinlm.test`: 토이 스토리 4 (TMDB 301528), 주먹왕 랄프 2: 인터넷 속으로 (TMDB 404368), 빅 히어로 (TMDB 177572), 퍼피 구조대: 더 마이티 무비 (TMDB 893723), 카 (TMDB 920)
- `v3seed-train-100@pinlm.test`: 페일 블루 아이 (TMDB 800815), 머시 (TMDB 5247), 더 보이 (TMDB 321258), The Curve (TMDB 44625), 살인소설 (TMDB 82507)
- `v3seed-train-101@pinlm.test`: 스팔타커스 (TMDB 967), Invasión (TMDB 294644), 배리 린든 (TMDB 3175), 킹 아더 (TMDB 9477), 씬 레드 라인 (TMDB 8741)
- `v3seed-train-102@pinlm.test`: 스폰 (TMDB 10336), 윗치 마운틴 (TMDB 13836), 드래곤볼 극장판 1: 신룡의 전설 (TMDB 39144), 저스티스 리그 다크 (TMDB 408220), 원피스 극장판 7기: 기계태엽성의 메카거병 (TMDB 44730)
- `v3seed-train-109@pinlm.test`: 패스트 & 퓨리어스 2 (TMDB 584), 분노의 질주 (TMDB 9799), 분노의 질주: 더 익스트림 (TMDB 337339), 분노의 질주: 더 세븐 (TMDB 168259), 리썰 웨폰 3 (TMDB 943)
- `v3seed-train-110@pinlm.test`: 카페 소사이어티 (TMDB 339397), 위아 유어 프렌즈 (TMDB 301351), 맨하탄 (TMDB 696), 네번의 결혼식과 한번의 장례식 (TMDB 712), 애니 홀 (TMDB 703)
- `v3seed-train-111@pinlm.test`: 더 보이 2: 돌아온 브람스 (TMDB 555974), 똑똑똑 (TMDB 631842), 페일 블루 아이 (TMDB 800815), 더 보이 (TMDB 321258), 엑스텐션 (TMDB 10226)
- `v3seed-train-112@pinlm.test`: 토이 스토리 4 (TMDB 301528), 크루즈 패밀리 (TMDB 49519), 헤라클레스 (TMDB 11970), 마다가스카 2 (TMDB 10527), 모아나 (TMDB 277834)
- `v3seed-train-113@pinlm.test`: 저스티스 리그: 갓 앤 몬스터 (TMDB 323027), 고지라: 파이널 워즈 (TMDB 15767), 저스티스 리그: 크라이시스 온 투 어스 (TMDB 30061), 파이널 판타지 7: 어드벤트 칠드런 (TMDB 647), 드래곤볼 에볼루션 (TMDB 14164)
- `v3seed-train-114@pinlm.test`: 쉰들러 리스트 (TMDB 424), 디파이언스 (TMDB 13813), 콰이강의 다리 (TMDB 826), Hiroshima (TMDB 68297), 판필로프 사단의 28 용사 (TMDB 427342)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
