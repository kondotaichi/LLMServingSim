# Routing flow summary (120 seconds)

Home GPU is nearest_gpu_id; destinations are the actual gpu_id values.
A destination equal to the home GPU means that the request was not redirected.

| Home GPU | Requests | A destinations | A redirect | B destinations | B redirect | C destinations | C redirect |
|---:|---:|---|---:|---|---:|---|---:|
| 0 | 24 | GPU 0: 24 | 0/24 (0.0%) | GPU 0: 24 | 0/24 (0.0%) | GPU 0: 24 | 0/24 (0.0%) |
| 1 | 30 | GPU 1: 30 | 0/30 (0.0%) | GPU 1: 30 | 0/30 (0.0%) | GPU 1: 30 | 0/30 (0.0%) |
| 2 | 22 | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) | GPU 2: 22 | 0/22 (0.0%) |
| 3 | 35 | GPU 3: 35 | 0/35 (0.0%) | GPU 3: 35 | 0/35 (0.0%) | GPU 3: 35 | 0/35 (0.0%) |
| 4 | 49 | GPU 4: 49 | 0/49 (0.0%) | GPU 4: 41, GPU 5: 8 | 8/49 (16.3%) | GPU 4: 41, GPU 5: 8 | 8/49 (16.3%) |
| 5 | 25 | GPU 5: 25 | 0/25 (0.0%) | GPU 5: 24, GPU 9: 1 | 1/25 (4.0%) | GPU 5: 25 | 0/25 (0.0%) |
| 6 | 30 | GPU 6: 30 | 0/30 (0.0%) | GPU 6: 30 | 0/30 (0.0%) | GPU 6: 30 | 0/30 (0.0%) |
| 7 | 29 | GPU 7: 29 | 0/29 (0.0%) | GPU 7: 29 | 0/29 (0.0%) | GPU 7: 29 | 0/29 (0.0%) |
| 8 | 30 | GPU 8: 30 | 0/30 (0.0%) | GPU 8: 30 | 0/30 (0.0%) | GPU 8: 30 | 0/30 (0.0%) |
| 9 | 26 | GPU 9: 26 | 0/26 (0.0%) | GPU 9: 26 | 0/26 (0.0%) | GPU 9: 26 | 0/26 (0.0%) |

## Policy totals

| Policy | Requests | Redirected | Redirect rate |
|---|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 0.0% |
| NEAREST_MIGRATE | 300 | 9 | 3.0% |
| NEAREST_MIGRATE_KV | 300 | 8 | 2.7% |
