# Routing flow summary (60 seconds)

Home GPU is nearest_gpu_id; destinations are the actual gpu_id values.
A destination equal to the home GPU means that the request was not redirected.

| Home GPU | Requests | A destinations | A redirect | B destinations | B redirect | C destinations | C redirect |
|---:|---:|---|---:|---|---:|---|---:|
| 0 | 24 | GPU 0: 24 | 0/24 (0.0%) | GPU 0: 19, GPU 1: 4, GPU 3: 1 | 5/24 (20.8%) | GPU 0: 22, GPU 1: 1, GPU 3: 1 | 2/24 (8.3%) |
| 1 | 30 | GPU 1: 30 | 0/30 (0.0%) | GPU 0: 5, GPU 1: 25 | 5/30 (16.7%) | GPU 0: 4, GPU 1: 26 | 4/30 (13.3%) |
| 2 | 22 | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) |
| 3 | 35 | GPU 3: 35 | 0/35 (0.0%) | GPU 0: 3, GPU 3: 29, GPU 4: 3 | 6/35 (17.1%) | GPU 0: 3, GPU 3: 29, GPU 4: 3 | 6/35 (17.1%) |
| 4 | 49 | GPU 4: 49 | 0/49 (0.0%) | GPU 4: 31, GPU 5: 18 | 18/49 (36.7%) | GPU 4: 30, GPU 5: 19 | 19/49 (38.8%) |
| 5 | 25 | GPU 5: 25 | 0/25 (0.0%) | GPU 5: 9, GPU 8: 7, GPU 9: 9 | 16/25 (64.0%) | GPU 5: 12, GPU 8: 6, GPU 9: 7 | 13/25 (52.0%) |
| 6 | 30 | GPU 6: 30 | 0/30 (0.0%) | GPU 5: 1, GPU 6: 29 | 1/30 (3.3%) | GPU 5: 3, GPU 6: 27 | 3/30 (10.0%) |
| 7 | 29 | GPU 7: 29 | 0/29 (0.0%) | GPU 7: 25, GPU 8: 4 | 4/29 (13.8%) | GPU 7: 24, GPU 8: 5 | 5/29 (17.2%) |
| 8 | 30 | GPU 8: 30 | 0/30 (0.0%) | GPU 5: 2, GPU 7: 5, GPU 8: 23 | 7/30 (23.3%) | GPU 5: 3, GPU 7: 6, GPU 8: 21 | 9/30 (30.0%) |
| 9 | 26 | GPU 9: 26 | 0/26 (0.0%) | GPU 5: 1, GPU 8: 1, GPU 9: 24 | 2/26 (7.7%) | GPU 8: 1, GPU 9: 25 | 1/26 (3.8%) |

## Policy totals

| Policy | Requests | Redirected | Redirect rate |
|---|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 0.0% |
| NEAREST_MIGRATE | 300 | 64 | 21.3% |
| NEAREST_MIGRATE_KV | 300 | 62 | 20.7% |
