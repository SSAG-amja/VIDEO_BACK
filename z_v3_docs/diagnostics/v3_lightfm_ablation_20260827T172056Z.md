# V3 LightFM Ablation

- generated_at: `2026-08-27T17:20:56.909727+00:00`
- same_dataset_hash: `True`
- representative_users: `24`
- top_k: `100`

## Model Summary

| representation | score min/median/max | unique movies | pairwise Jaccard | candidate seconds |
|---|---:|---:|---:|---:|
| supported_identity_normalized:u4.0+s0.25:i1.0+s1.0:freq-none:item-bias-learned:center-0.9:c32-e20-lr0.01 | -0.180 / -0.060 / 0.141 | 433 | 0.3377 | 0.660 |

## Profile Alignment

### supported_identity_normalized:u4.0+s0.25:i1.0+s1.0:freq-none:item-bias-learned:center-0.9:c32-e20-lr0.01

| profile | top20 overlap | top20 genre share | top100 overlap |
|---|---:|---:|---:|
| stable | 0.8583 | 0.4079 | 0.6067 |
| mixed | 0.9417 | 0.5779 | 0.7950 |
| drift | 0.4833 | 0.2104 | 0.4533 |
| negative_heavy | 0.6917 | 0.2915 | 0.4550 |

## Cross-Model Overlap

