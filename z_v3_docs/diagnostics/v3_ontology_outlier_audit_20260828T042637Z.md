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

- anomaly rows: `49`
- affected users: `12`
- unique movies: `25`
- rule counts: `{"cross_user_top5_repeat": 25, "drift_top5_old_only": 14, "high_negative_conflict": 17, "overbroad_catalog_genres": 1, "top10_low_vote": 1, "top10_no_current_genre": 31}`
- repeated top-5 occurrences outside current genres: `9`

## Top-5 Cohort Alignment

| profile | slots | current genre match | no current genre | historical only |
| --- | ---: | ---: | ---: | ---: |
| post_model_drift | 30 | 16 (53.3%) | 14 | 14 |
| post_model_stable | 30 | 30 (100.0%) | 0 | 0 |

## Repeated Top-5 Movies

| movie | users |
| --- | ---: |
| 어벤져스 (TMDB 24428) | 5 |
| 어벤져스: 인피니티 워 (TMDB 299536) | 5 |
| 포레스트 검프 (TMDB 13) | 3 |
| 펄프 픽션 (TMDB 680) | 3 |
| 셔터 아일랜드 (TMDB 11324) | 3 |
| 데드풀 (TMDB 293660) | 3 |
| 겟 아웃 (TMDB 419430) | 3 |

## Outliers And Ontology Evidence

| user | state | rank | movie | source | rules | long evidence | short evidence | negative evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 25 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.53) | genre:액션(3.64), theme:영웅성(1.16), mood:속도감(0.55), actor:Vin Diesel(0.34) | mood:감성적(0.31), mood:미스터리함(0.14); mood:감성적(0.03) |
| 25 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.53) | genre:액션(3.64), theme:영웅성(1.16), mood:속도감(0.55), mood:긴장감(0.32) | -; actor:Stellan Skarsgård(0.06) |
| 25 | post_model_stable | 5 | 펄프 픽션 (TMDB 680) | model | cross_user_top5_repeat | genre:스릴러(4.00), director:Quentin Tarantino(3.00), theme:범죄(2.38), mood:긴장감(2.17) | genre:스릴러(3.56), genre:범죄(3.56), theme:범죄(2.25), mood:긴장감(1.71) | genre:코미디(0.46), mood:로맨틱(0.23); mood:로맨틱(0.03) |
| 26 | post_model_stable | 1 | 포레스트 검프 (TMDB 13) | model+short_term_context | cross_user_top5_repeat | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), mood:로맨틱(2.63) | genre:코미디(3.48), genre:드라마(3.47), genre:로맨스(3.32), theme:사랑(2.43) | -; - |
| 26 | post_model_stable | 4 | 데드풀 (TMDB 293660) | model | cross_user_top5_repeat, high_negative_conflict | genre:코미디(4.00), mood:코믹(1.80), keyword:duringcreditsstinger(1.25) | genre:코미디(3.48), mood:코믹(1.17) | genre:액션(2.29), theme:영웅성(0.67); genre:모험(0.19), genre:액션(0.19) |
| 26 | post_model_stable | 5 | 펄프 픽션 (TMDB 680) | model | cross_user_top5_repeat, high_negative_conflict | genre:코미디(4.00), mood:코믹(1.80), theme:범죄(1.76), mood:로맨틱(0.76) | genre:코미디(3.48), mood:코믹(1.17), mood:로맨틱(0.69), keyword:los angeles, california(0.52) | genre:스릴러(2.35), genre:범죄(1.79); genre:스릴러(0.19), mood:긴장감(0.10) |
| 27 | post_model_stable | 1 | 겟 아웃 (TMDB 419430) | model+short_term_context | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), mood:미스터리함(2.58), mood:긴장감(2.58) | genre:공포(3.50), genre:스릴러(3.32), genre:미스터리(3.32), mood:미스터리함(2.19) | mood:코믹(0.21); mood:코믹(0.04), mood:어두움(0.03) |
| 27 | post_model_stable | 2 | 셔터 아일랜드 (TMDB 11324) | model | cross_user_top5_repeat | genre:드라마(4.00), genre:스릴러(4.00), mood:미스터리함(2.68), mood:긴장감(2.17) | genre:스릴러(3.32), genre:미스터리(3.32), mood:미스터리함(2.28), theme:미스터리(2.17) | -; - |
| 28 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), keyword:aftercreditsstinger(2.86), theme:영웅성(2.62), keyword:based on comic(2.48) | genre:모험(4.00), theme:모험(1.87), mood:긴장감(0.02) | genre:액션(1.72), genre:SF(1.72); genre:액션(0.20), genre:SF(0.20) |
| 29 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), keyword:aftercreditsstinger(3.00), keyword:based on comic(2.88) | genre:모험(4.00), genre:SF(3.96), theme:모험(1.59), theme:기술(0.79) | theme:모험(0.41), mood:감성적(0.30); genre:액션(0.19), theme:영웅성(0.09) |
| 29 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), keyword:aftercreditsstinger(3.00), keyword:based on comic(2.88) | genre:모험(4.00), genre:SF(3.96), theme:모험(1.59), theme:기술(0.79) | theme:모험(0.41); genre:액션(0.19), theme:영웅성(0.09) |
| 29 | post_model_stable | 4 | 데드풀 (TMDB 293660) | model | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), keyword:aftercreditsstinger(3.00), keyword:based on comic(2.88) | genre:모험(4.00), theme:모험(1.59) | theme:모험(0.41), mood:코믹(0.29); genre:액션(0.19), theme:영웅성(0.09) |
| 30 | post_model_stable | 8 | 파이트 클럽 (TMDB 550) | model | top10_no_current_genre | genre:드라마(4.00), mood:긴장감(1.51), mood:감성적(0.99) | mood:감성적(0.08) | -; - |
| 30 | post_model_stable | 9 | 타이타닉 (TMDB 597) | model | top10_no_current_genre | genre:드라마(4.00), keyword:based on true story(2.10), mood:감성적(0.99), mood:긴장감(0.89) | mood:감성적(0.08) | -; - |
| 35 | post_model_drift | 1 | 어벤져스 (TMDB 24428) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:영웅성(0.31), mood:웅장함(0.29) | mood:속도감(0.17), mood:긴장감(0.09); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:영웅성(0.31), mood:웅장함(0.29) | mood:감성적(0.38), mood:속도감(0.17); mood:감성적(0.07), theme:영웅성(0.05) |
| 35 | post_model_drift | 4 | 데드풀 (TMDB 293660) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), keyword:superhero(3.00), keyword:based on comic(2.95) | theme:영웅성(0.31), mood:웅장함(0.29), mood:어두움(0.01) | mood:속도감(0.17); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 6 | 가디언즈 오브 갤럭시 (TMDB 118340) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:based on comic(2.95) | mood:웅장함(0.12), theme:영웅성(0.12) | mood:속도감(0.17); mood:속도감(0.02), theme:영웅성(0.02) |
| 35 | post_model_drift | 8 | 해리 포터와 비밀의 방 (TMDB 672) | model | top10_no_current_genre | genre:모험(4.00), keyword:aftercreditsstinger(2.57), theme:모험(2.19), mood:미스터리함(0.53) | - | -; - |
| 35 | post_model_drift | 9 | 스파이더맨: 홈커밍 (TMDB 315635) | model | top10_no_current_genre, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), keyword:superhero(3.00) | theme:전쟁(0.72), theme:영웅성(0.31), mood:웅장함(0.29), mood:어두움(0.00) | theme:전쟁(0.57), mood:속도감(0.17); theme:전쟁(0.15), theme:영웅성(0.05) |
| 35 | post_model_drift | 10 | Hitler et Churchill : le combat de l'aigle et du lion (TMDB 512840) | short_term_context | top10_low_vote, high_negative_conflict | theme:전쟁(2.58), mood:웅장함(0.41) | genre:역사(3.61), genre:다큐멘터리(2.94), theme:전쟁(2.44), genre:전쟁(2.27) | genre:전쟁(2.00), theme:전쟁(1.92); genre:역사(0.49), theme:전쟁(0.49) |
| 35 | post_model_drift | 13 | 48 NOIDED MOVIES (TMDB 1529610) | short_term_context | overbroad_catalog_genres | genre:모험(4.00), genre:액션(4.00), genre:SF(4.00), theme:전쟁(2.43) | genre:역사(3.61), genre:다큐멘터리(2.94), theme:전쟁(2.30), genre:전쟁(2.27) | genre:전쟁(2.00), theme:전쟁(1.81); genre:역사(0.49), theme:전쟁(0.46) |
| 36 | post_model_drift | 1 | 라이언 일병 구하기 (TMDB 857) | model | drift_top5_old_only, top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.43), mood:감성적(1.82) | - | mood:웅장함(0.08); mood:웅장함(0.01) |
| 36 | post_model_drift | 3 | 핵소 고지 (TMDB 324786) | model | drift_top5_old_only, top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:biography(1.92) | - | mood:웅장함(0.08); mood:웅장함(0.01) |
| 36 | post_model_drift | 4 | 피아니스트 (TMDB 423) | model | drift_top5_old_only, top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:biography(1.92) | - | mood:긴장감(0.30); mood:긴장감(0.04) |
| 36 | post_model_drift | 6 | 덩케르크 (TMDB 374720) | model | top10_no_current_genre, high_negative_conflict | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:based on true story(1.60) | - | genre:액션(2.54), mood:속도감(0.41); genre:액션(0.47), keyword:europe(0.18) |
| 36 | post_model_drift | 8 | 이미테이션 게임 (TMDB 205596) | model | top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), theme:전쟁(2.58), keyword:biography(1.92) | mood:미스터리함(0.11) | mood:긴장감(0.59), mood:웅장함(0.08); mood:긴장감(0.07), mood:웅장함(0.01) |
| 36 | post_model_drift | 9 | 언브로큰 (TMDB 227306) | model | top10_no_current_genre | genre:전쟁(4.00), genre:드라마(4.00), keyword:based on novel or book(2.73), theme:전쟁(2.58) | keyword:based on novel or book(1.13) | -; - |
| 37 | post_model_drift | 1 | 포레스트 검프 (TMDB 13) | model+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:드라마(4.00), theme:사랑(2.43), mood:로맨틱(2.39) | genre:드라마(3.65), genre:로맨스(3.32), genre:코미디(3.32), theme:사랑(2.43) | genre:드라마(2.88), genre:로맨스(2.21); genre:로맨스(0.39), genre:드라마(0.39) |
| 37 | post_model_drift | 3 | 어벤져스 (TMDB 24428) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | - | -; - |
| 37 | post_model_drift | 4 | 어벤져스: 인피니티 워 (TMDB 299536) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | mood:감성적(0.45) | mood:감성적(0.33); mood:감성적(0.04) |
| 37 | post_model_drift | 7 | 가디언즈 오브 갤럭시 (TMDB 118340) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), keyword:based on comic(2.52), keyword:aftercreditsstinger(2.03) | - | -; - |
| 37 | post_model_drift | 9 | 토르: 천둥의 신 (TMDB 10195) | model | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(2.52) | - | theme:전쟁(0.17); mood:희망적(0.03) |
| 38 | post_model_drift | 1 | 겟 아웃 (TMDB 419430) | model+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:스릴러(4.00), mood:미스터리함(2.58), mood:긴장감(2.58), theme:미스터리(2.17) | genre:스릴러(3.97), genre:미스터리(3.56), genre:공포(3.32), mood:미스터리함(2.37) | genre:스릴러(2.89), mood:긴장감(1.59); genre:스릴러(0.31), mood:긴장감(0.19) |
| 38 | post_model_drift | 3 | 포레스트 검프 (TMDB 13) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:드라마(4.00), genre:코미디(4.00), keyword:based on novel or book(1.46), mood:감성적(0.89) | - | genre:드라마(1.50), keyword:based on novel or book(0.70); genre:드라마(0.31), mood:감성적(0.03) |
| 38 | post_model_drift | 4 | 셔터 아일랜드 (TMDB 11324) | model | cross_user_top5_repeat, high_negative_conflict | genre:드라마(4.00), genre:스릴러(4.00), mood:미스터리함(2.68), mood:긴장감(2.17) | genre:스릴러(3.97), genre:미스터리(3.56), mood:미스터리함(2.47), theme:미스터리(2.17) | genre:스릴러(2.89), genre:드라마(1.50); genre:드라마(0.31), genre:스릴러(0.31) |
| 38 | post_model_drift | 10 | 인사이드 아웃 (TMDB 150540) | model | top10_no_current_genre | genre:드라마(4.00), genre:코미디(4.00), theme:모험(1.22), mood:감성적(1.00) | - | genre:드라마(1.50), theme:모험(0.51); genre:드라마(0.31), theme:모험(0.05) |
| 39 | post_model_drift | 1 | 셔터 아일랜드 (TMDB 11324) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:드라마(4.00), genre:스릴러(4.00), mood:미스터리함(2.63), mood:긴장감(2.17) | keyword:based on novel or book(0.72), mood:미스터리함(0.47), mood:긴장감(0.39), mood:감성적(0.15) | -; mood:미스터리함(0.03) |
| 39 | post_model_drift | 3 | 펄프 픽션 (TMDB 680) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat | genre:스릴러(4.00), theme:범죄(2.38), mood:긴장감(2.17), keyword:neo-noir(1.31) | theme:범죄(0.46), mood:긴장감(0.39) | mood:사색적(0.05); - |
| 39 | post_model_drift | 4 | 파이트 클럽 (TMDB 550) | model | drift_top5_old_only, top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:긴장감(2.17), keyword:based on novel or book(1.90) | keyword:based on novel or book(0.72), mood:긴장감(0.39), mood:감성적(0.15) | -; - |
| 39 | post_model_drift | 6 | 그것 (TMDB 346364) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:긴장감(2.17), keyword:based on novel or book(1.90) | keyword:based on novel or book(0.72), mood:긴장감(0.39), mood:감성적(0.15) | -; - |
| 39 | post_model_drift | 7 | 나를 찾아줘 (TMDB 210577) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:미스터리함(2.53), mood:긴장감(2.17) | keyword:based on novel or book(0.72), mood:미스터리함(0.45), mood:긴장감(0.39), mood:감성적(0.15) | -; mood:미스터리함(0.03) |
| 39 | post_model_drift | 10 | 바스터즈: 거친 녀석들 (TMDB 16869) | model | top10_no_current_genre | genre:드라마(4.00), genre:스릴러(4.00), mood:긴장감(2.17), mood:감성적(1.27) | mood:긴장감(0.39), mood:감성적(0.27) | -; - |
| 58 | post_model_drift | 1 | 겟 아웃 (TMDB 419430) | model+short_term_context | cross_user_top5_repeat, high_negative_conflict | mood:미스터리함(2.58), mood:긴장감(2.25), theme:미스터리(2.17), mood:공포감(2.04) | genre:공포(3.32), genre:스릴러(3.32), genre:미스터리(3.32), mood:미스터리함(2.21) | genre:스릴러(2.96), mood:긴장감(1.74); genre:스릴러(0.25), mood:긴장감(0.16) |
| 58 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model | drift_top5_old_only, top10_no_current_genre, cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.23), theme:모험(1.92) | mood:미스터리함(0.66) | genre:액션(1.82), genre:SF(1.82); genre:모험(0.25), genre:액션(0.25) |
| 58 | post_model_drift | 4 | 해리 포터와 비밀의 방 (TMDB 672) | model | drift_top5_old_only, top10_no_current_genre | genre:모험(4.00), genre:판타지(4.00), theme:모험(2.19), keyword:aftercreditsstinger(1.60) | mood:미스터리함(0.90) | theme:모험(0.44), mood:미스터리함(0.17); genre:모험(0.25), theme:모험(0.14) |
| 58 | post_model_drift | 6 | 데드풀 (TMDB 293660) | model | top10_no_current_genre, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.23), theme:모험(1.92) | - | genre:액션(1.82), theme:영웅성(0.66); genre:모험(0.25), genre:액션(0.25) |
| 58 | post_model_drift | 8 | 인사이드 아웃 (TMDB 150540) | model | top10_no_current_genre | genre:모험(4.00), theme:모험(1.92), director:Pete Docter(1.40), actor:John Ratzenberger(0.43) | theme:가족 갈등(0.11) | theme:모험(0.38); genre:모험(0.25), theme:모험(0.12) |
| 58 | post_model_drift | 9 | 어벤져스 (TMDB 24428) | model | top10_no_current_genre, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.23), theme:모험(1.92) | mood:긴장감(0.29) | genre:액션(1.82), genre:SF(1.82); genre:모험(0.25), genre:액션(0.25) |

## Diagnosis

1. user 25 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
2. user 25 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
3. user 25 / rank 5 / 펄프 픽션: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
4. user 26 / rank 1 / 포레스트 검프: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
5. user 26 / rank 4 / 데드풀: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
6. user 26 / rank 5 / 펄프 픽션: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
7. user 27 / rank 1 / 겟 아웃: 
8. user 27 / rank 2 / 셔터 아일랜드: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
9. user 28 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
10. user 29 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
11. user 29 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
12. user 29 / rank 4 / 데드풀: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
13. user 30 / rank 8 / 파이트 클럽: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
14. user 30 / rank 9 / 타이타닉: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
15. user 35 / rank 1 / 어벤져스: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
16. user 35 / rank 3 / 어벤져스: 인피니티 워: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
17. user 35 / rank 4 / 데드풀: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
18. user 35 / rank 6 / 가디언즈 오브 갤럭시: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
19. user 35 / rank 8 / 해리 포터와 비밀의 방: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
20. user 35 / rank 9 / 스파이더맨: 홈커밍: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
21. user 35 / rank 10 / Hitler et Churchill : le combat de l'aigle et du lion: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
22. user 35 / rank 13 / 48 NOIDED MOVIES: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
23. user 36 / rank 1 / 라이언 일병 구하기: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
24. user 36 / rank 3 / 핵소 고지: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
25. user 36 / rank 4 / 피아니스트: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
26. user 36 / rank 6 / 덩케르크: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
27. user 36 / rank 8 / 이미테이션 게임: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
28. user 36 / rank 9 / 언브로큰: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
29. user 37 / rank 1 / 포레스트 검프: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
30. user 37 / rank 3 / 어벤져스: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
31. user 37 / rank 4 / 어벤져스: 인피니티 워: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
32. user 37 / rank 7 / 가디언즈 오브 갤럭시: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
33. user 37 / rank 9 / 토르: 천둥의 신: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
34. user 38 / rank 1 / 겟 아웃: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
35. user 38 / rank 3 / 포레스트 검프: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
36. user 38 / rank 4 / 셔터 아일랜드: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
37. user 38 / rank 10 / 인사이드 아웃: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
38. user 39 / rank 1 / 셔터 아일랜드: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
39. user 39 / rank 3 / 펄프 픽션: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
40. user 39 / rank 4 / 파이트 클럽: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
41. user 39 / rank 6 / 그것: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
42. user 39 / rank 7 / 나를 찾아줘: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
43. user 39 / rank 10 / 바스터즈: 거친 녀석들: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
44. user 58 / rank 1 / 겟 아웃: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
45. user 58 / rank 3 / 어벤져스: 인피니티 워: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5. Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
46. user 58 / rank 4 / 해리 포터와 비밀의 방: Long-term model dominance: high historical score and no short-term candidate source keep an old-cohort movie in the drift top-5.
47. user 58 / rank 6 / 데드풀: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
48. user 58 / rank 8 / 인사이드 아웃: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
49. user 58 / rank 9 / 어벤져스: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
