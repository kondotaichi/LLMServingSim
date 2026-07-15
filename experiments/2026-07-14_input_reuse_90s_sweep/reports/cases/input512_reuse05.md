# Input 512 / reuse 50% / 90s

## Result

| Policy | Requests | Redirects | Mean E2E TTFT | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 40.71 ms | 40.65 ms | 49.83 ms | 55.27 ms |
| NEAREST_MIGRATE | 300 | 0 | 40.71 ms | 40.65 ms | 49.83 ms | 55.27 ms |
| NEAREST_MIGRATE_KV | 300 | 0 | 40.71 ms | 40.65 ms | 49.83 ms | 55.27 ms |

All three policies are request-level identical. No router capacity rejection, redirect, or KV handoff occurred.

## Workload and breakdown

- Input tokens: 512
- Reused prefix: 256 tokens (50.0% effective; 50% nominal)
- Requests: 300 over 89.605 seconds
- Router queue mean: 0.00 ms
- Scheduler queue mean: 8.22 ms
- Compute / prefill mean: 32.49 ms
- Completion latency mean: 11.769 s
- Completion latency p95: 14.260 s

## Figures and data

- [E2E TTFT breakdown](../../figures/input512_reuse05/three_policy_ttft_breakdown.png)
- [E2E TTFT CDF](../../figures/input512_reuse05/three_policy_ttft_cdf.png)
- [E2E TTFT boxplot](../../figures/input512_reuse05/three_policy_ttft_boxplot.png)
- [Scheduler queue boxplot](../../figures/input512_reuse05/three_policy_scheduler_queue_boxplot.png)
- [Completion-latency CDF](../../figures/input512_reuse05/three_policy_completion_cdf.png)
- [GPU-level TTFT and queue](../../figures/input512_reuse05/gpu_ttft_and_queue.png)
- [Summary CSV](../../analysis/input512_reuse05/summary.csv)
