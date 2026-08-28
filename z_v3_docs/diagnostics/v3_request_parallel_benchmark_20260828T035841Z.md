# V3 Request Parallel Benchmark

- generated_at: `2026-08-28T03:58:41.632703+00:00`
- bundle: `bundle-21b4407076b864c2940b9fa3`
- users: `12` known users
- result invariant: `True`

| workers | trials | batch seconds | requests/sec | mean request | max p95 | peak RSS MiB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 32.424 | 0.370 | 2.702 | 3.273 | 937.8 |
| 2 | 2 | 21.329 | 0.563 | 3.551 | 3.998 | 937.8 |
| 4 | 2 | 15.751 | 0.762 | 5.166 | 5.872 | 937.8 |
