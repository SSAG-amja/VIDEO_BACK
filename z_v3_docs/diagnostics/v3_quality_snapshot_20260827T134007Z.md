# V3 장기·단기 추천 간이 품질 분석

- 생성 시각: `2026-08-27T13:40:07.696292+00:00`
- 사용자: 대표 `24`명 (유형별 6개 취향 cohort)
- 후보: 장기·단기·최종 각각 상위 `20`개
- 지표: `mean_genre_share`는 영화의 전체 장르 중 사용자 상위 장르와 일치한 비율의 평균이다.
- 주의: 이 값은 정답 기반 정확도가 아니라 방향을 확인하는 간이 지표다.

## 유형별 요약

| 유형 | drift | 장기 후보→장기 | 단기 후보→단기 | 최종→장기 | 최종→단기 | 최종 단기 source | 결과 수 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| stable | 0.065 | 0.310 | 0.329 | 0.555 | 0.153 | 0.000 | 20.0 |
| mixed | 0.350 | 0.268 | 0.733 | 0.307 | 0.293 | 0.042 | 20.0 |
| drift | 0.622 | 0.325 | 0.658 | 0.328 | 0.364 | 0.025 | 20.0 |
| negative_heavy | 0.022 | 0.311 | 0.186 | 0.439 | 0.116 | 0.008 | 20.0 |

## 사용자별 요약

| 사용자 | 유형 | 취향군 | 장기 장르 | 단기 장르 | drift | 최종→장기 | 최종→단기 | 단기 source |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| v3seed-train-001@pinlm.test | stable | action_crime_thriller | 모험, 액션, SF | 모험, 액션, SF | 0.258 | 0.495 | 0.495 | 0.000 |
| v3seed-train-002@pinlm.test | stable | romance_drama_comedy | 드라마, 코미디, 스릴러 | 모험, 애니메이션, 드라마 | 0.057 | 0.351 | 0.135 | 0.000 |
| v3seed-train-003@pinlm.test | stable | horror_mystery_thriller | 스릴러, 드라마, SF | 없음 | 0.000 | 0.381 | 0.000 | 0.000 |
| v3seed-train-004@pinlm.test | stable | animation_family_adventure | 모험, 판타지, 액션 | 없음 | 0.000 | 0.643 | 0.000 | 0.000 |
| v3seed-train-005@pinlm.test | stable | scifi_fantasy_adventure | 모험, 액션, SF | 없음 | 0.000 | 0.950 | 0.000 | 0.000 |
| v3seed-train-006@pinlm.test | stable | documentary_history_war | 전쟁, 드라마, 액션 | 드라마, 역사 | 0.072 | 0.509 | 0.291 | 0.000 |
| v3seed-train-073@pinlm.test | mixed | action_crime_thriller | 모험, 액션, SF | SF, 모험, 액션 | 0.477 | 0.260 | 0.260 | 0.000 |
| v3seed-train-074@pinlm.test | mixed | romance_drama_comedy | 드라마, 액션, SF | 범죄, 액션, 공포 | 0.503 | 0.371 | 0.196 | 0.050 |
| v3seed-train-075@pinlm.test | mixed | horror_mystery_thriller | 액션, 스릴러, 범죄 | 스릴러, 범죄, 공포 | 0.410 | 0.309 | 0.245 | 0.050 |
| v3seed-train-076@pinlm.test | mixed | animation_family_adventure | 모험, 액션, SF | 모험, 액션, SF | 0.419 | 0.381 | 0.381 | 0.100 |
| v3seed-train-077@pinlm.test | mixed | scifi_fantasy_adventure | 모험, 판타지, 액션 | 모험, 액션, 코미디 | 0.154 | 0.193 | 0.347 | 0.050 |
| v3seed-train-078@pinlm.test | mixed | documentary_history_war | 전쟁, 드라마, 역사 | 드라마, 역사, 전쟁 | 0.139 | 0.331 | 0.331 | 0.000 |
| v3seed-train-097@pinlm.test | drift | action_crime_thriller | 모험, 드라마, 액션 | 드라마, 코미디, 모험 | 0.611 | 0.292 | 0.529 | 0.000 |
| v3seed-train-098@pinlm.test | drift | romance_drama_comedy | 드라마, 액션, 스릴러 | 스릴러, SF, 드라마 | 0.579 | 0.501 | 0.374 | 0.000 |
| v3seed-train-099@pinlm.test | drift | horror_mystery_thriller | 모험, 판타지, 액션 | 모험, 판타지, 액션 | 0.636 | 0.117 | 0.117 | 0.000 |
| v3seed-train-100@pinlm.test | drift | animation_family_adventure | 모험, 드라마, 액션 | 스릴러, 드라마, 범죄 | 0.659 | 0.378 | 0.559 | 0.100 |
| v3seed-train-101@pinlm.test | drift | scifi_fantasy_adventure | 전쟁, 모험, 드라마 | 드라마, 역사, 전쟁 | 0.629 | 0.331 | 0.323 | 0.000 |
| v3seed-train-102@pinlm.test | drift | documentary_history_war | 모험, 드라마, 액션 | 모험, 액션, SF | 0.617 | 0.349 | 0.285 | 0.050 |
| v3seed-train-109@pinlm.test | negative_heavy | action_crime_thriller | 모험, 액션, 드라마 | 액션, 드라마, 모험 | 0.085 | 0.417 | 0.417 | 0.000 |
| v3seed-train-110@pinlm.test | negative_heavy | romance_drama_comedy | 드라마, 코미디, 스릴러 | 범죄, 드라마, 애니메이션 | 0.047 | 0.653 | 0.278 | 0.050 |
| v3seed-train-111@pinlm.test | negative_heavy | horror_mystery_thriller | 스릴러, 드라마, 범죄 | 없음 | 0.000 | 0.595 | 0.000 | 0.000 |
| v3seed-train-112@pinlm.test | negative_heavy | animation_family_adventure | 모험, 액션, 판타지 | 없음 | 0.000 | 0.225 | 0.000 | 0.000 |
| v3seed-train-113@pinlm.test | negative_heavy | scifi_fantasy_adventure | 모험, 액션, SF | 없음 | 0.000 | 0.368 | 0.000 | 0.000 |
| v3seed-train-114@pinlm.test | negative_heavy | documentary_history_war | 전쟁, 드라마, 역사 | 없음 | 0.000 | 0.374 | 0.000 | 0.000 |

## 최종 추천 표본

- `v3seed-train-001@pinlm.test`: 킬 빌: 2부 (TMDB 393), 어벤져스: 에이지 오브 울트론 (TMDB 99861), 다크 나이트 라이즈 (TMDB 49026), 아마겟돈 (TMDB 95), 드래곤볼 에볼루션 (TMDB 14164)
- `v3seed-train-002@pinlm.test`: 오만과 편견 그리고 좀비 (TMDB 58431), 잃어버린 세계 (TMDB 2981), 레드 라이딩 후드 (TMDB 49730), Privacy Breach (TMDB 936252), Buraczki (TMDB 1297831)
- `v3seed-train-003@pinlm.test`: 큐어 (TMDB 36095), 조디악 (TMDB 1949), 터미널 (TMDB 385332), 13 Gantry Row (TMDB 173795), L.A. 컨피덴셜 (TMDB 2118)
- `v3seed-train-004@pinlm.test`: 가디언즈 오브 갤럭시 Vol. 2 (TMDB 283995), 어벤져스: 에이지 오브 울트론 (TMDB 99861), 어벤져스: 엔드게임 (TMDB 299534), 캡틴 아메리카: 윈터 솔져 (TMDB 100402), 캡틴 마블 (TMDB 299537)
- `v3seed-train-005@pinlm.test`: 퍼스트 어벤져 (TMDB 1771), 캡틴 아메리카: 윈터 솔져 (TMDB 100402), 가디언즈 오브 갤럭시 Vol. 2 (TMDB 283995), 어벤져스: 에이지 오브 울트론 (TMDB 99861), 어벤져스: 엔드게임 (TMDB 299534)
- `v3seed-train-006@pinlm.test`: 얼라이드 (TMDB 369885), 독수리 착륙하다 (TMDB 11372), 13시간 (TMDB 300671), 더 캐쳐 워즈 어 스파이 (TMDB 467952), Takluk: Lahad Datu (TMDB 1208850)
- `v3seed-train-073@pinlm.test`: 위대한 쇼맨 (TMDB 316029), Max Headroom: 20 Minutes into the Future (TMDB 35933), 피아니스트 (TMDB 423), 그린 북 (TMDB 490132), The Hired Gun (TMDB 446282)
- `v3seed-train-074@pinlm.test`: 프리즈너스 (TMDB 146233), 킬 빌: 2부 (TMDB 393), 기생충 (TMDB 496243), 양들의 침묵 (TMDB 274), 드라이브 (TMDB 64690)
- `v3seed-train-075@pinlm.test`: 프리즈너스 (TMDB 146233), 라라랜드 (TMDB 313369), 아메리칸 메이드 (TMDB 337170), 기생충 (TMDB 496243), Mordkommission Calw - Klassentreffen (TMDB 1310208)
- `v3seed-train-076@pinlm.test`: 맨 인 블랙 (TMDB 607), 슈퍼배드 (TMDB 20352), 컨택트 (TMDB 329865), 샤이닝 (TMDB 694), 토이 스토리 2 (TMDB 863)
- `v3seed-train-077@pinlm.test`: 토이 스토리 2 (TMDB 863), 라라랜드 (TMDB 313369), 007 스카이폴 (TMDB 37724), 그린 북 (TMDB 490132), 혹성탈출: 진화의 시작 (TMDB 61791)
- `v3seed-train-078@pinlm.test`: 아메리칸 메이드 (TMDB 337170), 레드 라이트 (TMDB 75638), 빅쇼트 (TMDB 318846), 타이타닉 (TMDB 597), 머더 1600 (TMDB 9415)
- `v3seed-train-097@pinlm.test`: 그린 북 (TMDB 490132), 위대한 쇼맨 (TMDB 316029), 사랑시대 (TMDB 15143), 인 앤 아웃 (TMDB 10806), 피아니스트 (TMDB 423)
- `v3seed-train-098@pinlm.test`: 007 스카이폴 (TMDB 37724), 갱스 오브 뉴욕 (TMDB 3131), 혹성탈출: 진화의 시작 (TMDB 61791), 미션 임파서블 (TMDB 954), 피아니스트 (TMDB 423)
- `v3seed-train-099@pinlm.test`: 사랑시대 (TMDB 15143), 피아니스트 (TMDB 423), 그린 북 (TMDB 490132), 인 앤 아웃 (TMDB 10806), 위대한 쇼맨 (TMDB 316029)
- `v3seed-train-100@pinlm.test`: 프리즈너스 (TMDB 146233), 식스 센스 (TMDB 745), 타이타닉 (TMDB 597), 셔터 아일랜드 (TMDB 11324), 샤이닝 (TMDB 694)
- `v3seed-train-101@pinlm.test`: 기생충 (TMDB 496243), 라라랜드 (TMDB 313369), 그랜드 부다페스트 호텔 (TMDB 120467), 타이타닉 (TMDB 597), 프리즈너스 (TMDB 146233)
- `v3seed-train-102@pinlm.test`: 월•E (TMDB 10681), 퍼스트 어벤져 (TMDB 1771), 피아니스트 (TMDB 423), 토이 스토리 2 (TMDB 863), 분노의 질주: 더 세븐 (TMDB 168259)
- `v3seed-train-109@pinlm.test`: UFC on ESPN 33: Blaydes vs. Daukaus (TMDB 954362), 갱스 오브 뉴욕 (TMDB 3131), 미션 임파서블 (TMDB 954), The Hired Gun (TMDB 446282), 위대한 쇼맨 (TMDB 316029)
- `v3seed-train-110@pinlm.test`: 기생충 (TMDB 496243), 아메리칸 메이드 (TMDB 337170), 행오버 (TMDB 18785), 타이타닉 (TMDB 597), 프리즈너스 (TMDB 146233)
- `v3seed-train-111@pinlm.test`: 피아니스트 (TMDB 423), 갱스 오브 뉴욕 (TMDB 3131), 양들의 침묵 (TMDB 274), 위대한 쇼맨 (TMDB 316029), 연가시 (TMDB 121491)
- `v3seed-train-112@pinlm.test`: 라라랜드 (TMDB 313369), 퍼스트 어벤져 (TMDB 1771), 그랜드 부다페스트 호텔 (TMDB 120467), 맨 인 블랙 (TMDB 607), 언터처블: 1%의 우정 (TMDB 77338)
- `v3seed-train-113@pinlm.test`: 007 스카이폴 (TMDB 37724), 토이 스토리 2 (TMDB 863), 혹성탈출: 진화의 시작 (TMDB 61791), 레옹 (TMDB 101), 월•E (TMDB 10681)
- `v3seed-train-114@pinlm.test`: 피아니스트 (TMDB 423), 갱스 오브 뉴욕 (TMDB 3131), 위대한 쇼맨 (TMDB 316029), 히든 피겨스 (TMDB 381284), 인 앤 아웃 (TMDB 10806)

## 불변식

- 제외 영화 노출: `0`건
- 최종 중복: `0`건
