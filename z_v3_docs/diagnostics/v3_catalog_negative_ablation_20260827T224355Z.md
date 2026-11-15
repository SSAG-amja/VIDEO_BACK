# V3 Phase F Catalog And Negative Policy Ablation

- generated_at: `2026-08-27T22:43:55.585061+00:00`
- users: `18`
- bundle: `bundle-21b4407076b864c2940b9fa3`
- method: each user reuses one retrieval/ontology result across all policy variants

## Results

| profile | variant | vote=0 | vote 1-19 | vote>=20 | long genre | short genre | negative evidence | negative penalty | overlap | violations |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| post_model_stable | current | 0.000 | 0.000 | 1.000 | 0.631 | 0.502 | 0.519 | 0.070 | 1.000 | 0 |
| post_model_stable | catalog_soft | 0.000 | 0.000 | 1.000 | 0.631 | 0.502 | 0.519 | 0.070 | 1.000 | 0 |
| post_model_stable | negative_disabled | 0.000 | 0.000 | 1.000 | 0.587 | 0.472 | 0.660 | 0.000 | 0.867 | 0 |
| post_model_drift | current | 0.017 | 0.025 | 0.958 | 0.495 | 0.372 | 1.069 | 0.087 | 1.000 | 0 |
| post_model_drift | catalog_soft | 0.008 | 0.033 | 0.958 | 0.495 | 0.372 | 1.068 | 0.087 | 0.992 | 0 |
| post_model_drift | negative_disabled | 0.017 | 0.025 | 0.958 | 0.508 | 0.410 | 1.323 | 0.000 | 0.808 | 0 |
| negative_heavy | current | 0.008 | 0.158 | 0.833 | 0.548 | 0.168 | 1.662 | 0.117 | 1.000 | 0 |
| negative_heavy | catalog_soft | 0.000 | 0.167 | 0.833 | 0.548 | 0.168 | 1.662 | 0.117 | 0.967 | 0 |
| negative_heavy | negative_disabled | 0.008 | 0.158 | 0.833 | 0.525 | 0.170 | 2.199 | 0.000 | 0.892 | 0 |

## Interpretation Rules

- Catalog soft penalty is acceptable only when low-evidence exposure falls without a material genre-alignment loss.
- Current semantic-negative penalty is useful when its selected weighted-negative evidence is lower than the disabled variant.
- Exact passed/recent-negative exclusions must remain zero for every variant.
