# Rule-based vs LightFM 지표 비교 (확실한 지표만)

## ml-latest-small (248명)

| 지표 | Rule-based | LightFM(seed0) | 우세 |
|---|---|---|---|
| NDCG@20% ↑ | 0.0303 | 0.2011 | LightFM |
| Recall@20% ↑ | 0.0073 | 0.0414 | LightFM |
| NegHit@20% ↓ | 0.0142 | 0.0304 | Rule-based |
| 전체 NDCG ↑ | 0.0302 | 0.1799 | LightFM |
| Pairwise ↑ | 0.0254 | 0.1325 | LightFM |
| Coverage ↑ | 0.1051 | 0.1291 | LightFM |

## ml-32m 벤치마크 (10명)

| 지표 | Rule-based | LightFM(seed0) | 우세 |
|---|---|---|---|
| NDCG@20% ↑ | 0.0954 | 0.0930 | Rule-based |
| Recall@20% ↑ | 0.0159 | 0.0179 | LightFM |
| NegHit@20% ↓ | 0.0300 | 0.0400 | Rule-based |
| 전체 NDCG ↑ | 0.1090 | 0.0934 | Rule-based |
| Pairwise ↑ | 0.0994 | 0.0835 | Rule-based |
| Coverage ↑ | 0.1781 | 0.2776 | LightFM |
