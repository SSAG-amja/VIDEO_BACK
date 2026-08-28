# V3 Candidate Parallel Benchmark

- generated_at: `2026-08-28T03:46:44.632832+00:00`
- model: `hybrid-77a977915f6b-abb9c7b0706d-bf89dc0a3ba9-e0b8d8686041-a401dba670c5-7b869d3b`
- users: `100`
- user block: `32`
- item block: `8192`
- result invariant: `True`

| workers | trials | median seconds | users/sec | peak RSS MiB | score block MiB |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 2.667 | 37.50 | 966.9 | 1.0 |
| 2 | 2 | 2.838 | 35.24 | 966.9 | 2.0 |
| 4 | 2 | 2.758 | 36.28 | 966.7 | 3.1 |
