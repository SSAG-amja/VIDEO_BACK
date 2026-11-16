# V3 Ontology Outlier Audit

## Audit Scope

- users: `24`
- recommendations: `480` (top 20 per user)
- stable: `6`
- negative_heavy: `6`
- mixed: `6`
- drift: `6`
- ontology build: `22`
- interpretation: ontology matches explain the explicit semantic component, not LightFM's internal causal reason

## Cohorts

- `drift`: action_crime_thriller -> romance_drama_comedy; romance_drama_comedy -> horror_mystery_thriller; horror_mystery_thriller -> animation_family_adventure; animation_family_adventure -> horror_mystery_thriller; scifi_fantasy_adventure -> documentary_history_war; documentary_history_war -> scifi_fantasy_adventure
- `mixed`: action_crime_thriller -> action_crime_thriller; romance_drama_comedy -> romance_drama_comedy; horror_mystery_thriller -> horror_mystery_thriller; animation_family_adventure -> animation_family_adventure; scifi_fantasy_adventure -> scifi_fantasy_adventure; documentary_history_war -> documentary_history_war
- `negative_heavy`: action_crime_thriller -> action_crime_thriller; romance_drama_comedy -> romance_drama_comedy; horror_mystery_thriller -> horror_mystery_thriller; animation_family_adventure -> animation_family_adventure; scifi_fantasy_adventure -> scifi_fantasy_adventure; documentary_history_war -> documentary_history_war
- `stable`: action_crime_thriller -> action_crime_thriller; romance_drama_comedy -> romance_drama_comedy; horror_mystery_thriller -> horror_mystery_thriller; animation_family_adventure -> animation_family_adventure; scifi_fantasy_adventure -> scifi_fantasy_adventure; documentary_history_war -> documentary_history_war

## Summary

- anomaly rows: `42`
- affected users: `22`
- unique movies: `21`
- rule counts: `{"cross_user_top5_repeat": 23, "high_negative_conflict": 9, "overbroad_catalog_genres": 11, "top10_low_vote": 6, "top10_no_current_genre": 3}`
- repeated top-5 occurrences outside current genres: `0`

## Top-5 Cohort Alignment

| profile | slots | current genre match | no current genre | historical only |
| --- | ---: | ---: | ---: | ---: |
| drift | 30 | 30 (100.0%) | 0 | 0 |
| mixed | 30 | 29 (96.7%) | 1 | 0 |
| negative_heavy | 30 | 30 (100.0%) | 0 | 0 |
| stable | 30 | 30 (100.0%) | 0 | 0 |

## Repeated Top-5 Movies

| movie | users |
| --- | ---: |
| 토이 스토리 4 (TMDB 301528) | 4 |
| 페일 블루 아이 (TMDB 800815) | 4 |
| 카 (TMDB 920) | 3 |
| 빅 히어로 (TMDB 177572) | 3 |
| 더 보이 (TMDB 321258) | 3 |
| 분노의 질주: 더 익스트림 (TMDB 337339) | 3 |
| 카페 소사이어티 (TMDB 339397) | 3 |

## Outliers And Ontology Evidence

| user | state | rank | movie | source | rules | long evidence | short evidence | negative evidence |
| ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| 7 | stable | 2 | 분노의 질주: 더 익스트림 (TMDB 337339) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:액션(4.00), genre:스릴러(4.00), genre:범죄(4.00), mood:긴장감(2.39) | genre:액션(0.61), genre:스릴러(0.61), genre:범죄(0.61), theme:범죄(0.40) | -; - |
| 8 | stable | 1 | 카페 소사이어티 (TMDB 339397) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), theme:사랑(2.77) | genre:로맨스(1.43), genre:드라마(1.43), genre:코미디(1.43), theme:사랑(1.39) | mood:미스터리함(0.18), theme:초자연(0.09); genre:드라마(0.20), mood:미스터리함(0.02) |
| 9 | stable | 5 | 더 보이 (TMDB 321258) | model+long_term_ontology | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), mood:미스터리함(2.58) | genre:공포(1.47), genre:스릴러(1.47), genre:미스터리(1.47), theme:미스터리(1.03) | -; mood:미스터리함(0.02) |
| 10 | stable | 1 | 토이 스토리 4 (TMDB 301528) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | genre:가족(1.10), genre:모험(1.10), genre:애니메이션(1.10), theme:모험(0.61) | -; - |
| 10 | stable | 4 | 카 (TMDB 920) | model+long_term_ontology | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.59) | genre:가족(1.10), genre:모험(1.10), genre:애니메이션(1.10), theme:모험(0.72) | -; - |
| 11 | stable | 14 | The Amazing Screw-On Head (TMDB 213110) | long_term_ontology+short_term_context | overbroad_catalog_genres | genre:모험(4.00), genre:판타지(4.00), genre:SF(4.00), theme:모험(1.92) | genre:모험(0.91), genre:판타지(0.91), genre:SF(0.91), keyword:based on comic(0.44) | theme:전쟁(0.55), theme:영웅성(0.06); mood:웅장함(0.00) |
| 73 | mixed | 1 | 분노의 질주: 더 익스트림 (TMDB 337339) | model+long_term_ontology | cross_user_top5_repeat, high_negative_conflict | genre:액션(4.00), genre:스릴러(4.00), genre:범죄(4.00), mood:긴장감(2.39) | genre:액션(0.98), genre:스릴러(0.66), mood:긴장감(0.41), theme:범죄(0.41) | genre:스릴러(2.61), mood:긴장감(1.47); genre:스릴러(0.88), mood:긴장감(0.48) |
| 73 | mixed | 8 | 미니언즈 (TMDB 211672) | model | top10_no_current_genre | theme:모험(1.53) | genre:모험(0.63), theme:모험(0.25) | -; - |
| 73 | mixed | 9 | 헬보이: 폭풍의 검 (TMDB 16774) | long_term_ontology+short_term_context | overbroad_catalog_genres, high_negative_conflict | genre:액션(4.00), genre:스릴러(4.00), theme:영웅성(2.62), mood:긴장감(2.17) | genre:액션(0.98), genre:스릴러(0.66), genre:모험(0.63), theme:영웅성(0.40) | genre:공포(2.61), genre:스릴러(2.61); genre:공포(0.88), genre:스릴러(0.88) |
| 74 | mixed | 9 | ದಂಡಂ ದಶಗುಣಂ (TMDB 729156) | long_term_ontology+short_term_context | top10_low_vote | genre:로맨스(4.00), genre:드라마(4.00), genre:액션(4.00), mood:로맨틱(2.63) | genre:로맨스(0.71), genre:드라마(0.71), theme:사랑(0.70), mood:로맨틱(0.51) | mood:속도감(0.08); mood:속도감(0.06), mood:웅장함(0.02) |
| 74 | mixed | 10 | 분노의 질주 (TMDB 9799) | model | top10_no_current_genre | genre:액션(4.00), theme:범죄(2.17), mood:긴장감(1.93), keyword:los angeles, california(0.80) | theme:범죄(0.47), mood:긴장감(0.32) | mood:속도감(0.08); mood:속도감(0.06), mood:웅장함(0.01) |
| 74 | mixed | 16 | ASSR: le film (TMDB 1661243) | long_term_ontology+short_term_context | overbroad_catalog_genres | genre:로맨스(4.00), genre:드라마(4.00), genre:액션(4.00), mood:로맨틱(2.63) | genre:로맨스(0.71), genre:드라마(0.71), genre:코미디(0.71), theme:사랑(0.70) | genre:가족(2.04), genre:애니메이션(2.04); genre:가족(0.65), genre:애니메이션(0.65) |
| 75 | mixed | 2 | 페일 블루 아이 (TMDB 800815) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), theme:미스터리(2.73), mood:미스터리함(2.58) | genre:스릴러(1.12), theme:미스터리(0.75), genre:공포(0.73), genre:미스터리(0.73) | mood:미스터리함(0.24); mood:미스터리함(0.08) |
| 75 | mixed | 9 | City of Blood (TMDB 85206) | long_term_ontology+short_term_context | top10_low_vote | genre:공포(4.00), genre:스릴러(4.00), theme:범죄(2.77), mood:미스터리함(2.58) | genre:스릴러(1.12), genre:공포(0.73), genre:미스터리(0.73), mood:긴장감(0.61) | mood:미스터리함(0.24); mood:미스터리함(0.08) |
| 76 | mixed | 1 | 빅 히어로 (TMDB 177572) | model+long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:가족(4.00), genre:모험(4.00), theme:모험(1.92), mood:코믹(1.15) | genre:모험(1.04), genre:애니메이션(1.04), genre:가족(0.64), theme:모험(0.45) | theme:모험(0.30), mood:속도감(0.22); theme:영웅성(0.10), mood:웅장함(0.06) |
| 76 | mixed | 4 | 토이 스토리 4 (TMDB 301528) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), theme:모험(2.19), mood:코믹(1.15) | genre:모험(1.04), genre:애니메이션(1.04), genre:가족(0.64), theme:모험(0.52) | theme:모험(0.35), mood:감성적(0.06); mood:감성적(0.02) |
| 76 | mixed | 5 | 카 (TMDB 920) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), theme:모험(2.59), mood:코믹(1.15) | genre:모험(1.04), genre:애니메이션(1.04), genre:가족(0.64), theme:모험(0.61) | theme:모험(0.41), mood:감성적(0.06); mood:감성적(0.02) |
| 77 | mixed | 2 | 빅 히어로 (TMDB 177572) | model+long_term_ontology | cross_user_top5_repeat, high_negative_conflict | genre:가족(4.00), genre:모험(4.00), theme:모험(1.92), theme:영웅성(1.52) | genre:모험(0.34), genre:액션(0.18), theme:모험(0.17), mood:코믹(0.07) | genre:액션(2.34), theme:영웅성(0.72); genre:모험(0.48), genre:액션(0.48) |
| 77 | mixed | 12 | Codename: Kids Next Door: Operation Z.E.R.O. (TMDB 205081) | long_term_ontology | overbroad_catalog_genres | genre:가족(4.00), genre:모험(4.00), genre:판타지(4.00), theme:모험(1.92) | genre:모험(0.34), genre:판타지(0.34), genre:액션(0.18), theme:모험(0.17) | genre:액션(2.34), theme:미스터리(0.54); genre:모험(0.48), genre:액션(0.48) |
| 78 | mixed | 4 | 분노의 질주 (TMDB 9799) | model | top10_no_current_genre | theme:영웅성(0.28) | mood:속도감(0.03) | -; - |
| 78 | mixed | 12 | Tour de Cinema: The 21st Century - Part One (TMDB 1484504) | long_term_ontology | overbroad_catalog_genres | genre:로맨스(4.00), genre:전쟁(4.00), genre:드라마(4.00), mood:로맨틱(2.63) | genre:로맨스(0.25), genre:드라마(0.25), genre:코미디(0.25), theme:사랑(0.21) | genre:로맨스(2.30), genre:드라마(2.30); genre:로맨스(0.49), genre:드라마(0.49) |
| 78 | mixed | 13 | HMS 25-26 (TMDB 1640459) | long_term_ontology | overbroad_catalog_genres | genre:로맨스(4.00), genre:전쟁(4.00), genre:드라마(4.00), mood:로맨틱(2.63) | genre:로맨스(0.25), genre:드라마(0.25), genre:코미디(0.25), theme:사랑(0.21) | genre:로맨스(2.30), genre:드라마(2.30); genre:로맨스(0.49), genre:드라마(0.49) |
| 97 | drift | 5 | 카페 소사이어티 (TMDB 339397) | long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), theme:사랑(2.77) | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), theme:사랑(2.77) | keyword:los angeles, california(1.10); keyword:los angeles, california(0.86) |
| 98 | drift | 2 | 페일 블루 아이 (TMDB 800815) | short_term_context | cross_user_top5_repeat | genre:공포(4.00), theme:미스터리(2.73), mood:미스터리함(2.58), mood:공포감(2.43) | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:미스터리(2.73) | theme:범죄(0.52); theme:범죄(0.43) |
| 99 | drift | 1 | 토이 스토리 4 (TMDB 301528) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | -; - |
| 99 | drift | 3 | 빅 히어로 (TMDB 177572) | model+long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(1.92) | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(1.92) | mood:긴장감(0.74); mood:긴장감(0.61) |
| 99 | drift | 4 | 퍼피 구조대: 더 마이티 무비 (TMDB 893723) | short_term_context | top10_low_vote | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | -; - |
| 99 | drift | 5 | 카 (TMDB 920) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.59) | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.59) | -; - |
| 100 | drift | 1 | 페일 블루 아이 (TMDB 800815) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:미스터리(2.73) | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:미스터리(2.73) | mood:미스터리함(0.34); mood:미스터리함(0.29) |
| 100 | drift | 3 | 더 보이 (TMDB 321258) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), mood:미스터리함(2.58) | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), mood:미스터리함(2.58) | mood:미스터리함(0.34); mood:미스터리함(0.29) |
| 101 | drift | 7 | Tour de Cinema: The 21st Century - Part One (TMDB 1484504) | long_term_ontology+short_term_context | top10_low_vote, overbroad_catalog_genres, high_negative_conflict | genre:전쟁(4.00), genre:역사(4.00), genre:다큐멘터리(4.00), theme:전쟁(2.43) | genre:전쟁(4.00), genre:역사(4.00), genre:다큐멘터리(3.63), theme:전쟁(2.43) | genre:모험(2.51), genre:판타지(2.51); genre:모험(2.09), genre:판타지(2.09) |
| 101 | drift | 10 | Scars Of Nanking (TMDB 551291) | short_term_context | top10_low_vote | genre:전쟁(4.00), genre:역사(4.00), genre:다큐멘터리(4.00), theme:전쟁(2.92) | genre:전쟁(4.00), genre:역사(4.00), genre:다큐멘터리(3.63), theme:전쟁(2.92) | mood:웅장함(0.05); mood:웅장함(0.04) |
| 102 | drift | 8 | 마비취 (TMDB 142300) | short_term_context | top10_low_vote | genre:모험(4.00), genre:판타지(4.00), genre:액션(4.00), theme:모험(1.92) | genre:모험(4.00), genre:판타지(4.00), genre:SF(4.00), theme:모험(1.92) | theme:모험(0.46), mood:속도감(0.30); theme:모험(0.42), mood:속도감(0.26) |
| 102 | drift | 9 | The Amazing Screw-On Head (TMDB 213110) | long_term_ontology+short_term_context | overbroad_catalog_genres, high_negative_conflict | genre:모험(4.00), genre:판타지(4.00), genre:액션(4.00), theme:모험(1.92) | genre:모험(4.00), genre:판타지(4.00), genre:SF(4.00), theme:모험(1.92) | theme:전쟁(0.74), theme:모험(0.46); theme:전쟁(0.62), theme:모험(0.42) |
| 102 | drift | 14 | The Grim Adventures of the Kids Next Door (TMDB 361472) | short_term_context | overbroad_catalog_genres | genre:모험(4.00), genre:판타지(4.00), genre:액션(4.00), theme:모험(1.92) | genre:모험(4.00), genre:판타지(4.00), genre:SF(4.00), theme:모험(1.92) | theme:모험(0.46), mood:속도감(0.30); theme:모험(0.42), mood:속도감(0.26) |
| 102 | drift | 19 | Codename: Kids Next Door: Operation Z.E.R.O. (TMDB 205081) | short_term_context | overbroad_catalog_genres | genre:모험(4.00), genre:판타지(4.00), genre:액션(4.00), theme:모험(1.92) | genre:모험(4.00), genre:판타지(4.00), genre:SF(4.00), theme:모험(1.92) | theme:모험(0.46), mood:속도감(0.30); theme:모험(0.42), mood:속도감(0.26) |
| 109 | negative_heavy | 3 | 분노의 질주: 더 익스트림 (TMDB 337339) | model+long_term_ontology+short_term_context | cross_user_top5_repeat | genre:액션(4.00), genre:스릴러(4.00), genre:범죄(4.00), mood:긴장감(2.39) | genre:액션(0.97), genre:스릴러(0.97), genre:범죄(0.97), theme:범죄(0.69) | -; - |
| 110 | negative_heavy | 1 | 카페 소사이어티 (TMDB 339397) | model+long_term_ontology+short_term_context | cross_user_top5_repeat, high_negative_conflict | genre:로맨스(4.00), genre:드라마(4.00), genre:코미디(4.00), theme:사랑(2.77) | director:Woody Allen(0.39), genre:로맨스(0.38), genre:드라마(0.38), genre:코미디(0.38) | theme:초자연(0.31), mood:미스터리함(0.30); mood:미스터리함(0.30), theme:초자연(0.24) |
| 111 | negative_heavy | 3 | 페일 블루 아이 (TMDB 800815) | model+long_term_ontology | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), theme:미스터리(2.73) | - | -; - |
| 111 | negative_heavy | 4 | 더 보이 (TMDB 321258) | model+long_term_ontology | cross_user_top5_repeat | genre:공포(4.00), genre:스릴러(4.00), genre:미스터리(4.00), mood:미스터리함(2.58) | - | -; - |
| 112 | negative_heavy | 1 | 토이 스토리 4 (TMDB 301528) | model+long_term_ontology | cross_user_top5_repeat | genre:가족(4.00), genre:모험(4.00), genre:애니메이션(4.00), theme:모험(2.19) | - | -; - |
| 113 | negative_heavy | 13 | 헬보이: 폭풍의 검 (TMDB 16774) | long_term_ontology | overbroad_catalog_genres | genre:모험(3.93), genre:판타지(3.93), genre:SF(3.93), theme:모험(1.65) | - | theme:전쟁(0.76), theme:모험(0.69); theme:전쟁(0.76), theme:모험(0.59) |

## Diagnosis

1. user 7 / rank 2 / 분노의 질주: 더 익스트림: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
2. user 8 / rank 1 / 카페 소사이어티: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
3. user 9 / rank 5 / 더 보이: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
4. user 10 / rank 1 / 토이 스토리 4: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
5. user 10 / rank 4 / 카: 
6. user 11 / rank 14 / The Amazing Screw-On Head: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
7. user 73 / rank 1 / 분노의 질주: 더 익스트림: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
8. user 73 / rank 8 / 미니언즈: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
9. user 73 / rank 9 / 헬보이: 폭풍의 검: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
10. user 74 / rank 9 / ದಂಡಂ ದಶಗುಣಂ: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
11. user 74 / rank 10 / 분노의 질주: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
12. user 74 / rank 16 / ASSR: le film: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
13. user 75 / rank 2 / 페일 블루 아이: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
14. user 75 / rank 9 / City of Blood: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
15. user 76 / rank 1 / 빅 히어로: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
16. user 76 / rank 4 / 토이 스토리 4: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
17. user 76 / rank 5 / 카: 
18. user 77 / rank 2 / 빅 히어로: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
19. user 77 / rank 12 / Codename: Kids Next Door: Operation Z.E.R.O.: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
20. user 78 / rank 4 / 분노의 질주: Long-term model spillover: a high historical model score keeps an off-current-cohort movie in ranks 6-10.
21. user 78 / rank 12 / Tour de Cinema: The 21st Century - Part One: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
22. user 78 / rank 13 / HMS 25-26: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
23. user 97 / rank 5 / 카페 소사이어티: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
24. user 98 / rank 2 / 페일 블루 아이: 
25. user 99 / rank 1 / 토이 스토리 4: 
26. user 99 / rank 3 / 빅 히어로: Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
27. user 99 / rank 4 / 퍼피 구조대: 더 마이티 무비: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
28. user 99 / rank 5 / 카: 
29. user 100 / rank 1 / 페일 블루 아이: 
30. user 100 / rank 3 / 더 보이: 
31. user 101 / rank 7 / Tour de Cinema: The 21st Century - Part One: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap. Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
32. user 101 / rank 10 / Scars Of Nanking: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
33. user 102 / rank 8 / 마비취: Catalog trust is insufficient for the rank: the soft penalty lowers the score but cannot remove a lane-forced candidate.
34. user 102 / rank 9 / The Amazing Screw-On Head: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
35. user 102 / rank 14 / The Grim Adventures of the Kids Next Door: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
36. user 102 / rank 19 / Codename: Kids Next Door: Operation Z.E.R.O.: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
37. user 109 / rank 3 / 분노의 질주: 더 익스트림: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
38. user 110 / rank 1 / 카페 소사이어티: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles. Positive/negative evidence conflict: the candidate matches both preference directions and survives the bounded negative cap.
39. user 111 / rank 3 / 페일 블루 아이: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
40. user 111 / rank 4 / 더 보이: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
41. user 112 / rank 1 / 토이 스토리 4: Cross-user model concentration: a high normalized long-term score repeatedly promotes this movie across profiles.
42. user 113 / rank 13 / 헬보이: 폭풍의 검: Catalog metadata amplification: an unusually broad genre list creates many ontology matches and can satisfy unrelated cohorts.
