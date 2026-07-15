# Input 512 / reuse 25% / 90s

## Result

| Policy | Requests | Redirects | Mean E2E TTFT | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 53.45 ms | 53.73 ms | 63.01 ms | 66.58 ms |
| NEAREST_MIGRATE | 300 | 0 | 53.45 ms | 53.73 ms | 63.01 ms | 66.58 ms |
| NEAREST_MIGRATE_KV | 300 | 0 | 53.45 ms | 53.73 ms | 63.01 ms | 66.58 ms |

All three policies are request-level identical. No router capacity rejection, redirect, or KV handoff occurred.

## Workload and breakdown

- Input tokens: 512
- Reused prefix: 128 tokens (25.0% effective; 25% nominal)
- Requests: 300 over 89.605 seconds
- Router queue mean: 0.00 ms
- Scheduler queue mean: 8.60 ms
- Compute / prefill mean: 44.86 ms
- Completion latency mean: 11.829 s
- Completion latency p95: 14.333 s

## Figures and data

- [E2E TTFT breakdown](../../figures/input512_reuse025/three_policy_ttft_breakdown.png)
- [E2E TTFT CDF](../../figures/input512_reuse025/three_policy_ttft_cdf.png)
- [E2E TTFT boxplot](../../figures/input512_reuse025/three_policy_ttft_boxplot.png)
- [Scheduler queue boxplot](../../figures/input512_reuse025/three_policy_scheduler_queue_boxplot.png)
- [Completion-latency CDF](../../figures/input512_reuse025/three_policy_completion_cdf.png)
- [GPU-level TTFT and queue](../../figures/input512_reuse025/gpu_ttft_and_queue.png)
- [Summary CSV](../../analysis/input512_reuse025/summary.csv)
