# Routing flow summary (90 seconds)

Home GPU is nearest_gpu_id; destinations are the actual gpu_id values.
A destination equal to the home GPU means that the request was not redirected.

| Home GPU | Requests | A destinations | A redirect | B destinations | B redirect | C destinations | C redirect |
|---:|---:|---|---:|---|---:|---|---:|
| 0 | 24 | GPU 0: 24 | 0/24 (0.0%) | GPU 0: 24 | 0/24 (0.0%) | GPU 0: 24 | 0/24 (0.0%) |
| 1 | 30 | GPU 1: 30 | 0/30 (0.0%) | GPU 1: 30 | 0/30 (0.0%) | GPU 1: 30 | 0/30 (0.0%) |
| 2 | 22 | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) |
| 3 | 35 | GPU 3: 35 | 0/35 (0.0%) | GPU 3: 34, GPU 4: 1 | 1/35 (2.9%) | GPU 3: 33, GPU 4: 2 | 2/35 (5.7%) |
| 4 | 49 | GPU 4: 49 | 0/49 (0.0%) | GPU 4: 36, GPU 5: 13 | 13/49 (26.5%) | GPU 4: 36, GPU 5: 13 | 13/49 (26.5%) |
| 5 | 25 | GPU 5: 25 | 0/25 (0.0%) | GPU 5: 20, GPU 8: 4, GPU 9: 1 | 5/25 (20.0%) | GPU 5: 21, GPU 8: 3, GPU 9: 1 | 4/25 (16.0%) |
| 6 | 30 | GPU 6: 30 | 0/30 (0.0%) | GPU 5: 3, GPU 6: 27 | 3/30 (10.0%) | GPU 5: 3, GPU 6: 27 | 3/30 (10.0%) |
| 7 | 29 | GPU 7: 29 | 0/29 (0.0%) | GPU 7: 29 | 0/29 (0.0%) | GPU 7: 29 | 0/29 (0.0%) |
| 8 | 30 | GPU 8: 30 | 0/30 (0.0%) | GPU 7: 2, GPU 8: 28 | 2/30 (6.7%) | GPU 7: 2, GPU 8: 28 | 2/30 (6.7%) |
| 9 | 26 | GPU 9: 26 | 0/26 (0.0%) | GPU 9: 26 | 0/26 (0.0%) | GPU 9: 26 | 0/26 (0.0%) |

## Policy totals

| Policy | Requests | Redirected | Redirect rate |
|---|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 0.0% |
| NEAREST_MIGRATE | 300 | 24 | 8.0% |
| NEAREST_MIGRATE_KV | 300 | 24 | 8.0% |
