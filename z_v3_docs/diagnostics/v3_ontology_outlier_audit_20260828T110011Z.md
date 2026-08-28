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

- anomaly rows: `34`
- affected users: `11`
- unique movies: `22`
- rule counts: `{"cross_user_top5_repeat": 13, "high_negative_conflict": 10, "overbroad_catalog_genres": 3, "top10_low_vote": 8, "top10_no_current_genre": 15}`
- repeated top-5 occurrences outside current genres: `4`

## Top-5 Cohort Alignment

| profile | slots | current genre match | no current genre | historical only |
| --- | ---: | ---: | ---: | ---: |
| post_model_drift | 30 | 22 (73.3%) | 8 | 8 |
| post_model_stable | 30 | 30 (100.0%) | 0 | 0 |

## Repeated Top-5 Movies

| movie | users |
| --- | ---: |
| 어벤져스 (TMDB 24428) | 5 |
| 어벤져스: 인피니티 워 (TMDB 299536) | 5 |
| 겟 아웃 (TMDB 419430) | 3 |

## Outliers And Ontology Evidence

| user | state | rank | movie | source | rules | long evidence | short evidence | negative evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 25 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model+long_term_ontology | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.05) | genre:액션(3.63), theme:영웅성(1.16), mood:속도감(0.55), actor:Vin Diesel(0.34) | mood:감성적(0.25), mood:미스터리함(0.09); mood:감성적(0.03) |
| 25 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model+long_term_ontology | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.05) | genre:액션(3.63), theme:영웅성(1.16), mood:속도감(0.55), mood:긴장감(0.32) | -; actor:Stellan Skarsgård(0.06) |
| 26 | post_model_stable | 6 | 괜찮아... 그냥 섹스라니까 (TMDB 84586) | long_term_ontology+short_term_context | top10_low_vote | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), mood:로맨틱(2.63) | genre:코미디(3.48), genre:드라마(3.47), genre:로맨스(3.31), theme:사랑(2.43) | mood:긴장감(0.37); mood:긴장감(0.04) |
| 27 | post_model_stable | 1 | 겟 아웃 (TMDB 419430) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), mood:미스터리함(2.58) | genre:공포(3.49), genre:스릴러(3.31), genre:미스터리(3.31), mood:미스터리함(2.19) | mood:코믹(0.17); mood:코믹(0.04), mood:어두움(0.03) |
| 27 | post_model_stable | 5 | City of Blood (TMDB 85206) | long_term_ontology+short_term_context | top10_low_vote | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:범죄(2.77) | genre:공포(3.49), genre:스릴러(3.31), genre:미스터리(3.31), mood:미스터리함(2.19) | -; genre:범죄(0.16), theme:범죄(0.12) |
| 27 | post_model_stable | 6 | 13 Gantry Row (TMDB 173795) | long_term_ontology+short_term_context | top10_low_vote | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:범죄(2.77) | genre:공포(3.49), genre:스릴러(3.31), genre:미스터리(3.31), mood:미스터리함(2.19) | -; genre:범죄(0.16), theme:범죄(0.12) |
| 28 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model+long_term_ontology | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), theme:영웅성(2.59), keyword:aftercreditsstinger(2.25), keyword:duringcreditsstinger(1.97) | genre:모험(4.00), theme:모험(1.87), mood:긴장감(0.02) | genre:액션(1.38), genre:SF(1.38); genre:액션(0.20), genre:SF(0.20) |
| 28 | post_model_stable | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model+long_term_ontology | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), theme:영웅성(2.59), keyword:aftercreditsstinger(2.25), theme:모험(1.92) | genre:모험(4.00), theme:모험(1.87), mood:감성적(0.17), mood:미스터리함(0.06) | genre:액션(1.38), genre:SF(1.38); genre:액션(0.20), genre:SF(0.20) |
| 29 | post_model_stable | 1 | 어벤져스: 인피니티 워 (TMDB 299536) | model+long_term_ontology | cross_user_top5_repeat, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.21) | genre:모험(4.00), genre:SF(3.95), theme:모험(1.59), theme:기술(0.79) | theme:모험(0.31), mood:감성적(0.24); genre:액션(0.19), theme:영웅성(0.09) |
| 29 | post_model_stable | 2 | 어벤져스 (TMDB 24428) | model+long_term_ontology | cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), theme:영웅성(2.62), keyword:aftercreditsstinger(2.21) | genre:모험(4.00), genre:SF(3.95), theme:모험(1.59), theme:기술(0.79) | theme:모험(0.31); genre:액션(0.19), theme:영웅성(0.09) |
| 35 | post_model_drift | 1 | 어벤져스 (TMDB 24428) | model+long_term_ontology | top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:영웅성(0.31), mood:웅장함(0.29) | mood:속도감(0.13), mood:긴장감(0.06); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 2 | Hitler et Churchill : le combat de l'aigle et du lion (TMDB 512840) | short_term_context | top10_low_vote, high_negative_conflict | theme:전쟁(2.58), mood:웅장함(0.31) | genre:역사(3.61), genre:다큐멘터리(2.93), theme:전쟁(2.44), genre:전쟁(2.27) | genre:전쟁(1.64), theme:전쟁(1.60); genre:역사(0.49), theme:전쟁(0.49) |
| 35 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model+long_term_ontology | top10_no_current_genre, cross_user_top5_repeat | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:영웅성(0.31), mood:웅장함(0.29) | mood:감성적(0.32), mood:속도감(0.13); mood:감성적(0.07), theme:영웅성(0.05) |
| 35 | post_model_drift | 4 | 48 NOIDED MOVIES (TMDB 1529610) | short_term_context | top10_low_vote, overbroad_catalog_genres, high_negative_conflict | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:전쟁(2.43) | genre:역사(3.61), genre:다큐멘터리(2.93), theme:전쟁(2.30), genre:전쟁(2.27) | genre:전쟁(1.64), theme:전쟁(1.51); genre:역사(0.49), theme:전쟁(0.46) |
| 35 | post_model_drift | 5 | 캡틴 아메리카: 시빌 워 (TMDB 271110) | model+long_term_ontology | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:전쟁(0.72), mood:웅장함(0.46), theme:영웅성(0.31) | theme:전쟁(0.48), mood:속도감(0.13); theme:전쟁(0.15), theme:영웅성(0.05) |
| 35 | post_model_drift | 6 | 블랙 팬서 (TMDB 284054) | model+long_term_ontology | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:전쟁(0.72), theme:영웅성(0.31), mood:웅장함(0.29) | theme:전쟁(0.48), mood:속도감(0.13); theme:전쟁(0.15), theme:영웅성(0.05) |
| 35 | post_model_drift | 8 | 어벤져스: 엔드게임 (TMDB 299534) | model+long_term_ontology | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:전쟁(0.72), theme:영웅성(0.31), mood:웅장함(0.29) | theme:전쟁(0.48), mood:속도감(0.13); theme:전쟁(0.15), theme:영웅성(0.05) |
| 35 | post_model_drift | 9 | 앤트맨 (TMDB 102899) | model+long_term_ontology | top10_no_current_genre | genre:모험(4.00), genre:액션(4.00), genre:SF(3.87), theme:영웅성(2.62) | theme:영웅성(0.31), mood:웅장함(0.29) | mood:속도감(0.13); theme:영웅성(0.05), mood:웅장함(0.03) |
| 35 | post_model_drift | 10 | Les Nazis et l'Argent : au cœur du IIIe Reich (TMDB 792977) | short_term_context | top10_low_vote, high_negative_conflict | theme:전쟁(2.58), mood:웅장함(0.31) | genre:역사(3.61), genre:다큐멘터리(2.93), theme:전쟁(2.44), genre:전쟁(2.27) | genre:전쟁(1.64), theme:전쟁(1.60); genre:역사(0.49), theme:전쟁(0.49) |
| 36 | post_model_drift | 3 | 쉰들러 리스트 (TMDB 424) | model+long_term_ontology | top10_no_current_genre | genre:드라마(4.00), genre:역사(4.00), theme:전쟁(2.58), keyword:based on novel or book(2.35) | keyword:based on novel or book(1.13) | theme:영웅성(0.52), mood:웅장함(0.14); theme:영웅성(0.15), mood:웅장함(0.03) |
| 36 | post_model_drift | 8 | 라이언 일병 구하기 (TMDB 857) | model+long_term_ontology | top10_no_current_genre | genre:드라마(4.00), genre:역사(4.00), theme:전쟁(2.43), mood:감성적(1.68) | - | mood:웅장함(0.06); mood:웅장함(0.01) |
| 37 | post_model_drift | 1 | 어벤져스 (TMDB 24428) | model+long_term_ontology | top10_no_current_genre, cross_user_top5_repeat | genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(1.65), keyword:superhero(1.51) | - | -; - |
| 37 | post_model_drift | 3 | 어벤져스: 인피니티 워 (TMDB 299536) | model+long_term_ontology | top10_no_current_genre, cross_user_top5_repeat | genre:액션(4.00), theme:영웅성(2.62), keyword:based on comic(1.65), keyword:superhero(1.51) | mood:감성적(0.45) | mood:감성적(0.25); mood:감성적(0.04) |
| 37 | post_model_drift | 19 | O Incidente (TMDB 783098) | long_term_ontology | overbroad_catalog_genres | genre:드라마(4.00), genre:액션(4.00), genre:코미디(4.00), theme:사랑(2.43) | genre:드라마(3.65), genre:로맨스(3.31), genre:코미디(3.31), theme:사랑(2.43) | genre:드라마(2.25), genre:로맨스(1.84); genre:로맨스(0.39), genre:드라마(0.39) |
| 38 | post_model_drift | 1 | 겟 아웃 (TMDB 419430) | model+long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:스릴러(4.00), genre:미스터리(3.90), mood:미스터리함(2.58), mood:긴장감(2.58) | genre:스릴러(3.96), genre:미스터리(3.55), genre:공포(3.31), mood:미스터리함(2.37) | genre:스릴러(2.36), mood:긴장감(1.30); genre:스릴러(0.31), mood:긴장감(0.19) |
| 38 | post_model_drift | 7 | City of Blood (TMDB 85206) | long_term_ontology+short_term_context | top10_low_vote, high_negative_conflict | genre:드라마(4.00), genre:스릴러(4.00), genre:미스터리(3.90), mood:미스터리함(2.58) | genre:스릴러(3.96), genre:미스터리(3.55), genre:공포(3.31), mood:미스터리함(2.37) | genre:스릴러(2.36), genre:드라마(1.31); genre:드라마(0.31), genre:스릴러(0.31) |
| 38 | post_model_drift | 10 | 13 Gantry Row (TMDB 173795) | long_term_ontology+short_term_context | top10_low_vote, high_negative_conflict | genre:드라마(4.00), genre:스릴러(4.00), genre:미스터리(3.90), mood:미스터리함(2.58) | genre:스릴러(3.96), genre:미스터리(3.55), genre:공포(3.31), mood:미스터리함(2.37) | genre:스릴러(2.36), genre:드라마(1.31); genre:드라마(0.31), genre:스릴러(0.31) |
| 39 | post_model_drift | 1 | 프리즈너스 (TMDB 146233) | model+long_term_ontology | top10_no_current_genre | genre:스릴러(4.00), genre:드라마(3.86), mood:긴장감(2.43), theme:범죄(2.17) | mood:긴장감(0.44), theme:범죄(0.42), mood:미스터리함(0.20), mood:감성적(0.15) | -; actor:Jake Gyllenhaal(0.04), mood:미스터리함(0.01) |
| 39 | post_model_drift | 3 | 세븐 (TMDB 807) | model+long_term_ontology | top10_no_current_genre | genre:스릴러(4.00), theme:범죄(2.77), mood:긴장감(2.17), mood:미스터리함(2.06) | theme:범죄(0.54), mood:미스터리함(0.45), mood:긴장감(0.39) | -; mood:미스터리함(0.03) |
| 39 | post_model_drift | 6 | 키스 더 걸 (TMDB 9437) | long_term_ontology | top10_no_current_genre | genre:스릴러(4.00), genre:드라마(3.86), theme:범죄(2.77), mood:긴장감(2.17) | keyword:based on novel or book(0.72), theme:범죄(0.54), mood:미스터리함(0.45), mood:긴장감(0.39) | -; mood:미스터리함(0.03) |
| 39 | post_model_drift | 7 | 조디악 (TMDB 1949) | long_term_ontology | top10_no_current_genre | genre:스릴러(4.00), theme:범죄(2.77), mood:긴장감(2.17), mood:미스터리함(2.06) | keyword:based on novel or book(0.72), theme:범죄(0.54), mood:미스터리함(0.45), mood:긴장감(0.39) | -; actor:Jake Gyllenhaal(0.04), mood:미스터리함(0.03) |
| 39 | post_model_drift | 10 | 본 콜렉터 (TMDB 9481) | long_term_ontology | top10_no_current_genre | genre:스릴러(4.00), genre:드라마(3.86), theme:범죄(2.77), mood:긴장감(2.17) | keyword:based on novel or book(0.72), theme:범죄(0.54), mood:미스터리함(0.45), mood:긴장감(0.39) | -; mood:미스터리함(0.03) |
| 58 | post_model_drift | 1 | 겟 아웃 (TMDB 419430) | model+long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:공포(3.43), genre:스릴러(3.43), mood:미스터리함(2.58), theme:미스터리(2.17) | genre:공포(3.31), genre:스릴러(3.31), genre:미스터리(3.31), mood:미스터리함(2.21) | genre:스릴러(2.31), mood:긴장감(1.36); genre:스릴러(0.25), mood:긴장감(0.16) |
| 58 | post_model_drift | 20 | Buraczki (TMDB 1297831) | long_term_ontology | overbroad_catalog_genres | genre:모험(4.00), genre:공포(3.43), genre:스릴러(3.43), mood:미스터리함(2.58) | genre:공포(3.31), genre:스릴러(3.31), genre:미스터리(3.31), mood:미스터리함(2.21) | genre:스릴러(2.31), genre:액션(1.55); genre:모험(0.25), genre:액션(0.25) |

## Diagnosis

1. user 25 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
2. user 25 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
3. user 26 / rank 6 / 괜찮아... 그냥 섹스라니까: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
4. user 27 / rank 1 / 겟 아웃: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
5. user 27 / rank 5 / City of Blood: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
6. user 27 / rank 6 / 13 Gantry Row: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
7. user 28 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
8. user 28 / rank 3 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
9. user 29 / rank 1 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
10. user 29 / rank 2 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
11. user 35 / rank 1 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
12. user 35 / rank 2 / Hitler et Churchill : le combat de l'aigle et du lion: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
13. user 35 / rank 3 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
14. user 35 / rank 4 / 48 NOIDED MOVIES: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
15. user 35 / rank 5 / 캡틴 아메리카: 시빌 워: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
16. user 35 / rank 6 / 블랙 팬서: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
17. user 35 / rank 8 / 어벤져스: 엔드게임: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
18. user 35 / rank 9 / 앤트맨: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
19. user 35 / rank 10 / Les Nazis et l'Argent : au cœur du IIIe Reich: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
20. user 36 / rank 3 / 쉰들러 리스트: 
21. user 36 / rank 8 / 라이언 일병 구하기: 
22. user 37 / rank 1 / 어벤져스: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
23. user 37 / rank 3 / 어벤져스: 인피니티 워: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
24. user 37 / rank 19 / O Incidente: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
25. user 38 / rank 1 / 겟 아웃: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
26. user 38 / rank 7 / City of Blood: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
27. user 38 / rank 10 / 13 Gantry Row: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
28. user 39 / rank 1 / 프리즈너스: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
29. user 39 / rank 3 / 세븐: 
30. user 39 / rank 6 / 키스 더 걸: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
31. user 39 / rank 7 / 조디악: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
32. user 39 / rank 10 / 본 콜렉터: Non-genre ontology support: keyword/person/theme/mood links raise a candidate that does not match the current target genres.
33. user 58 / rank 1 / 겟 아웃: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
34. user 58 / rank 20 / Buraczki: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
