# V3 Phase E ontology component ablation

- generated: `2026-08-27T22:28:05.297145+00:00`
- users: `12` post-model users
- bundle: `bundle-77128ec4c5c9b5404efc3b4b`
- comparison: personal/ontology `1.00/0.00` vs `0.75/0.25` on the same retrieval candidates

## Profile comparison

| profile | top20 overlap | long genre 0%→25% | short genre 0%→25% | short-only 0%→25% |
| --- | ---: | ---: | ---: | ---: |
| post_model_stable | 0.742 | 0.302→0.409 | 0.214→0.303 | 0.000→0.000 |
| post_model_drift | 0.758 | 0.271→0.345 | 0.372→0.398 | 0.342→0.342 |

## Family contribution

- `genre`: 0.551
- `mood`: 0.208
- `theme`: 0.156
- `keyword`: 0.070
- `director`: 0.009
- `actor`: 0.005

## Score and semantic diagnostics

- `model` mean ontology/base share: 0.242
- `short_term_context` mean ontology/base share: 0.475
- genre-only mean rank uplift: -4.750
- theme/mood-supported mean rank uplift: 6.101
