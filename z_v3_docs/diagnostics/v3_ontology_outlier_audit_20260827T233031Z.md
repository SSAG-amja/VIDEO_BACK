# V3 Ontology Outlier Audit

## Audit Scope

- users: `12`
- post_model_stable: `6`
- post_model_drift: `6`
- recommendations: `240` (top 20 per user)
- ontology build: `22`
- interpretation: ontology matches explain the explicit semantic component, not LightFM's internal causal reason

## Cohorts

- `post_model_drift`: action_crime_thriller -> romance_drama_comedy; romance_drama_comedy -> horror_mystery_thriller; horror_mystery_thriller -> animation_family_adventure; animation_family_adventure -> horror_mystery_thriller; scifi_fantasy_adventure -> documentary_history_war; documentary_history_war -> scifi_fantasy_adventure
- `post_model_stable`: action_crime_thriller -> action_crime_thriller; romance_drama_comedy -> romance_drama_comedy; horror_mystery_thriller -> horror_mystery_thriller; animation_family_adventure -> animation_family_adventure; scifi_fantasy_adventure -> scifi_fantasy_adventure; documentary_history_war -> documentary_history_war

## Summary

- anomaly rows: `40`
- affected users: `11`
- unique movies: `25`
- rule counts: `{"cross_user_top5_repeat": 17, "drift_top5_old_only": 12, "high_negative_conflict": 11, "overbroad_catalog_genres": 1, "top10_low_vote": 1, "top10_no_current_genre": 29}`
- repeated top-5 occurrences outside current genres: `7`

## Top-5 Cohort Alignment

| profile | slots | current genre match | no current genre | historical only |
| --- | ---: | ---: | ---: | ---: |
| post_model_drift | 30 | 18 (60.0%) | 12 | 12 |
| post_model_stable | 30 | 30 (100.0%) | 0 | 0 |

## Repeated Top-5 Movies

| movie | users |
| --- | ---: |
| 어벤져스: 인피니티 워 (TMDB 299536) | 5 |
| 펄프 픽션 (TMDB 680) | 4 |
| 어벤져스 (TMDB 24428) | 4 |
| 데드풀 (TMDB 293660) | 4 |

## Outliers And Ontology Evidence

| user | state | rank | movie | source | rules | long evidence | short evidence | negative evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 25 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.53) | genre:액션(3.68), theme:영웅성(1.18), mood:속도감(0.56), actor:Vin Diesel(0.35) | mood:감성적(0.31), mood:미스터리함(0.14); mood:감성적(0.03) |
| 25 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.53) | genre:액션(3.68), theme:영웅성(1.18), mood:속도감(0.56), mood:긴장감(0.32) | -; actor:Stellan Skarsgård(0.06) |
| 25 | post_model_stable | 5 | 데드풀 (TMDB 293660) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.53) | genre:액션(3.68), theme:영웅성(1.18), mood:속도감(0.56), mood:어두움(0.32) | genre:코미디(0.46); - |
| 26 | post_model_stable | 5 | 펄프 픽션 (TMDB 680) | model | cross_user_top5_repeat, high_negative_conflict | genre:코미디(4.00), mood:코믹(1.80), theme:범죄(1.76), mood:로맨틱(0.76) | genre:코미디(3.52), mood:코믹(1.19), mood:로맨틱(0.70), keyword:los angeles, california(0.53) | genre:스릴러(2.35), genre:범죄(1.79); genre:스릴러(0.19), mood:긴장감(0.10) |
| 27 | post_model_stable | 4 | 펄프 픽션 (TMDB 680) | model | cross_user_top5_repeat | genre:스릴러(4.00), theme:범죄(2.38), mood:긴장감(2.17), director:Quentin Tarantino(1.30) | genre:스릴러(3.36), mood:긴장감(1.64) | mood:코믹(0.35); genre:코미디(0.16), genre:범죄(0.16) |
| 28 | post_model_stable | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), keyword:aftercreditsstinger(2.86), theme:영웅성(2.62), keyword:based on comic(2.48) | genre:모험(4.00), theme:모험(1.89), mood:감성적(0.17), mood:미스터리함(0.06) | genre:액션(1.72), genre:SF(1.72); genre:액션(0.20), genre:SF(0.20) |
| 28 | post_model_stable | 4 | 어벤져스 (TMDB 24428) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), keyword:aftercreditsstinger(2.86), theme:영웅성(2.62), keyword:based on comic(2.48) | genre:모험(4.00), theme:모험(1.89), mood:긴장감(0.02) | genre:액션(1.72), genre:SF(1.72); genre:액션(0.20), genre:SF(0.20) |
| 29 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), keyword:aftercreditsstinger(3.00), keyword:based on comic(2.88) | genre:모험(4.00), genre:SF(4.00), theme:모험(1.61), theme:기술(0.80) | theme:모험(0.41), mood:감성적(0.30); genre:액션(0.19), theme:영웅성(0.09) |
| 35 | post_model_drift | 1 | 어벤져스 (TMDB 24428) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:영웅성(0.31), mood:웅장함(0.30) | mood:속도감(0.17), mood:긴장감(0.09); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:영웅성(0.31), mood:웅장함(0.30) | mood:감성적(0.38), mood:속도감(0.17); mood:감성적(0.07), theme:영웅성(0.05) |
| 35 | post_model_drift | 4 | 데드풀 (TMDB 293660) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), keyword:superhero(3.00), keyword:based on comic(2.95) | theme:영웅성(0.31), mood:웅장함(0.30), mood:어두움(0.01) | mood:속도감(0.17); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 6 | 토르: 다크 월드 (TMDB 76338) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), keyword:superhero(3.00), keyword:based on comic(2.95) | theme:영웅성(0.31), mood:웅장함(0.30) | mood:속도감(0.17), mood:감성적(0.12); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 8 | 가디언즈 오브 갤럭시 (TMDB 118340) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:based on comic(2.95) | mood:웅장함(0.12), theme:영웅성(0.12) | mood:속도감(0.17); mood:속도감(0.02), theme:영웅성(0.02) |
| 35 | post_model_drift | 9 | 앤트맨 (TMDB 102899) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:영웅성(0.31), mood:웅장함(0.30) | mood:속도감(0.17); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 10 | 48 NOIDED MOVIES (TMDB 1529610) | short_term_context | top10_low_vote, overbroad_catalog_genres, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), theme:전쟁(2.43) | genre:역사(3.66), genre:다큐멘터리(2.97), theme:전쟁(2.33), genre:전쟁(2.30) | genre:전쟁(2.00), theme:전쟁(1.81); genre:역사(0.50), theme:전쟁(0.47) |
| 36 | post_model_drift | 1 | 라이언 일병 구하기 (TMDB 857) | model | drift_top5_old_only, top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.43), mood:감성적(1.82) | - | mood:웅장함(0.08); mood:웅장함(0.01) |
| 36 | post_model_drift | 4 | 피아니스트 (TMDB 423) | model | drift_top5_old_only, top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:biography(1.92) | - | mood:긴장감(0.30); mood:긴장감(0.04) |
| 36 | post_model_drift | 6 | 덩케르크 (TMDB 374720) | model | top10_no_current_genre, high_negative_conflict | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:based on true story(1.60) | - | genre:액션(2.54), mood:속도감(0.41); genre:액션(0.48), keyword:europe(0.18) |
| 36 | post_model_drift | 8 | 오펜하이머 (TMDB 872585) | model | top10_no_current_genre | genre:드라마(4.00), keyword:based on novel or book(2.73), keyword:biography(1.92), keyword:based on true story(1.60) | keyword:based on novel or book(1.15) | mood:웅장함(0.08); mood:웅장함(0.01) |
| 36 | post_model_drift | 9 | 이미테이션 게임 (TMDB 205596) | model | top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:biography(1.92) | mood:미스터리함(0.11) | mood:긴장감(0.59), mood:웅장함(0.08); mood:긴장감(0.07), mood:웅장함(0.01) |
| 37 | post_model_drift | 1 | 어벤져스 (TMDB 24428) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | - | -; - |
| 37 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | mood:감성적(0.46) | mood:감성적(0.33); mood:감성적(0.04) |
| 37 | post_model_drift | 4 | 데드풀 (TMDB 293660) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | genre:코미디(3.36), mood:코믹(1.08) | genre:코미디(0.94), mood:코믹(0.32); - |
| 37 | post_model_drift | 6 | 캡틴 아메리카: 시빌 워 (TMDB 271110) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | - | theme:전쟁(0.17); - |
| 37 | post_model_drift | 7 | 블랙 팬서 (TMDB 284054) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | - | theme:전쟁(0.17); - |
| 37 | post_model_drift | 9 | 가디언즈 오브 갤럭시 (TMDB 118340) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), keyword:based on comic(2.52), keyword:aftercreditsstinger(2.03) | - | -; - |
| 38 | post_model_drift | 3 | 포레스트 검프 (TMDB 13) | model | drift_top5_old_only, top10_no_current_genre | genre:드라마(4.00), genre:코미디(4.00), keyword:based on novel or book(1.46), mood:감성적(0.89) | - | genre:드라마(1.50), keyword:based on novel or book(0.70); genre:드라마(0.31), mood:감성적(0.03) |
| 38 | post_model_drift | 4 | 펄프 픽션 (TMDB 680) | model | cross_user_top5_repeat, high_negative_conflict | genre:코미디(4.00), genre:스릴러(4.00), mood:긴장감(2.17), theme:범죄(1.59) | genre:스릴러(4.00), mood:긴장감(1.93), theme:범죄(0.43) | genre:스릴러(2.89), mood:긴장감(1.34); genre:스릴러(0.31), mood:긴장감(0.16) |
| 38 | post_model_drift | 10 | 슈퍼배드 (TMDB 20352) | model | top10_no_current_genre | genre:코미디(4.00), theme:범죄(1.45), mood:감성적(0.17) | theme:범죄(0.39) | theme:범죄(0.59), mood:감성적(0.03); mood:감성적(0.01) |
| 39 | post_model_drift | 3 | 셔터 아일랜드 (TMDB 11324) | model | drift_top5_old_only, top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:미스터리함(2.63), mood:긴장감(2.17) | keyword:based on novel or book(0.73), mood:미스터리함(0.48), mood:긴장감(0.39), mood:감성적(0.15) | -; mood:미스터리함(0.04) |
| 39 | post_model_drift | 4 | 펄프 픽션 (TMDB 680) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:스릴러(4.00), theme:범죄(2.38), mood:긴장감(2.17), keyword:neo-noir(1.31) | theme:범죄(0.47), mood:긴장감(0.39) | mood:사색적(0.05); - |
| 39 | post_model_drift | 6 | 파이트 클럽 (TMDB 550) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:긴장감(2.17), keyword:based on novel or book(1.90) | keyword:based on novel or book(0.73), mood:긴장감(0.39), mood:감성적(0.15) | -; - |
| 39 | post_model_drift | 7 | 조커 (TMDB 475557) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), theme:범죄(2.38), mood:긴장감(2.17) | theme:범죄(0.47), mood:긴장감(0.39), mood:감성적(0.15) | keyword:based on comic(0.76); - |
| 39 | post_model_drift | 9 | 양들의 침묵 (TMDB 274) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), theme:범죄(2.77), mood:긴장감(2.17) | keyword:based on novel or book(0.73), theme:범죄(0.55), mood:긴장감(0.39), mood:감성적(0.15) | -; - |
| 39 | post_model_drift | 10 | 그린 마일 (TMDB 497) | model | top10_no_current_genre | genre:드라마(4.00), theme:범죄(2.17), keyword:based on novel or book(1.90), mood:미스터리함(1.03) | keyword:based on novel or book(0.73), theme:범죄(0.43), mood:미스터리함(0.19), mood:감성적(0.15) | -; mood:미스터리함(0.01) |
| 58 | post_model_drift | 3 | 해리 포터와 죽음의 성물 2 (TMDB 12445) | model | drift_top5_old_only, top10_no_current_genre | genre:모험(4.00), genre:판타지(4.00), director:David Yates(2.40), theme:모험(1.92) | mood:미스터리함(0.34) | theme:모험(0.38), mood:미스터리함(0.06); genre:모험(0.26), theme:모험(0.12) |
| 58 | post_model_drift | 4 | 데드풀 (TMDB 293660) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.23), theme:모험(1.92) | - | genre:액션(1.82), theme:영웅성(0.66); genre:모험(0.26), genre:액션(0.26) |
| 58 | post_model_drift | 6 | 어벤져스: 인피니티 워 (TMDB 299536) | model | top10_no_current_genre, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.23), theme:모험(1.92) | mood:미스터리함(0.66) | genre:액션(1.82), genre:SF(1.82); genre:모험(0.26), genre:액션(0.26) |
| 58 | post_model_drift | 8 | 토르: 천둥의 신 (TMDB 10195) | model | top10_no_current_genre, high_negative_conflict | genre:모험(4.00), genre:판타지(4.00), genre:액션(4.00), theme:영웅성(2.23) | mood:미스터리함(0.34) | genre:액션(1.82), theme:영웅성(0.66); genre:모험(0.26), genre:액션(0.26) |
| 58 | post_model_drift | 9 | 몬스터 주식회사 (TMDB 585) | model | top10_no_current_genre | director:Pete Docter(1.40), actor:John Ratzenberger(0.43) | theme:가족 갈등(0.11) | -; - |

## Diagnosis

1. user 25 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
2. user 25 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
3. user 25 / rank 5 / 데드풀: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
4. user 26 / rank 5 / 펄프 픽션: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
5. user 27 / rank 4 / 펄프 픽션: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
6. user 28 / rank 3 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
7. user 28 / rank 4 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
8. user 29 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
9. user 35 / rank 1 / 어벤져스: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
10. user 35 / rank 3 / 어벤져스: 인피니티 워: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
11. user 35 / rank 4 / 데드풀: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
12. user 35 / rank 6 / 토르: 다크 월드: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
13. user 35 / rank 8 / 가디언즈 오브 갤럭시: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
14. user 35 / rank 9 / 앤트맨: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
15. user 35 / rank 10 / 48 NOIDED MOVIES: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
16. user 36 / rank 1 / 라이언 일병 구하기: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
17. user 36 / rank 4 / 피아니스트: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
18. user 36 / rank 6 / 덩케르크: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
19. user 36 / rank 8 / 오펜하이머: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
20. user 36 / rank 9 / 이미테이션 게임: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
21. user 37 / rank 1 / 어벤져스: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
22. user 37 / rank 3 / 어벤져스: 인피니티 워: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
23. user 37 / rank 4 / 데드풀: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
24. user 37 / rank 6 / 캡틴 아메리카: 시빌 워: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
25. user 37 / rank 7 / 블랙 팬서: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
26. user 37 / rank 9 / 가디언즈 오브 갤럭시: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
27. user 38 / rank 3 / 포레스트 검프: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
28. user 38 / rank 4 / 펄프 픽션: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
29. user 38 / rank 10 / 슈퍼배드: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
30. user 39 / rank 3 / 셔터 아일랜드: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
31. user 39 / rank 4 / 펄프 픽션: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
32. user 39 / rank 6 / 파이트 클럽: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
33. user 39 / rank 7 / 조커: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
34. user 39 / rank 9 / 양들의 침묵: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
35. user 39 / rank 10 / 그린 마일: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
36. user 58 / rank 3 / 해리 포터와 죽음의 성물 2: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
37. user 58 / rank 4 / 데드풀: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
38. user 58 / rank 6 / 어벤져스: 인피니티 워: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
39. user 58 / rank 8 / 토르: 천둥의 신: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
40. user 58 / rank 9 / 몬스터 주식회사: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
