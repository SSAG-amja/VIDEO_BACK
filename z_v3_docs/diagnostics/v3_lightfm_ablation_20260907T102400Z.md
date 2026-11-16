# V3 LightFM Ablation

- generated_at: `2026-09-07T10:24:00.937551+00:00`
- same_dataset_hash: `True`
- representative_users: `24`
- top_k: `100`

## Model Summary

| representation | score min/median/max | unique movies | pairwise Jaccard | candidate seconds |
|---|---:|---:|---:|---:|
| supported_identity_normalized:u2.0+s0.5:i1.0+s1.0:freq-inverse_sqrt:item-bias-learned:center-0.9:c32-e20-lr0.01 | -0.040 / 0.063 / 0.267 | 585 | 0.1619 | 0.668 |

## Profile Alignment

### supported_identity_normalized:u2.0+s0.5:i1.0+s1.0:freq-inverse_sqrt:item-bias-learned:center-0.9:c32-e20-lr0.01

| profile | top20 overlap | top20 genre share | top100 overlap |
|---|---:|---:|---:|
| stable | 0.8583 | 0.7044 | 0.8200 |
| mixed | 0.8500 | 0.7521 | 0.8283 |
| drift | 0.3417 | 0.2989 | 0.4017 |
| negative_heavy | 0.8917 | 0.7554 | 0.8600 |

## Cross-Model Overlap

