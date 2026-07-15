# Input 2000 / reuse 25% / 90s

## Result

| Policy | Requests | Redirects | Mean E2E TTFT | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 180.16 ms | 176.38 ms | 198.50 ms | 297.03 ms |
| NEAREST_MIGRATE | 300 | 0 | 180.16 ms | 176.38 ms | 198.50 ms | 297.03 ms |
| NEAREST_MIGRATE_KV | 300 | 0 | 180.16 ms | 176.38 ms | 198.50 ms | 297.03 ms |

All three policies are request-level identical. No router capacity rejection, redirect, or KV handoff occurred.

## Workload and breakdown

- Input tokens: 2000
- Reused prefix: 496 tokens (24.8% effective; 25% nominal)
- Requests: 300 over 89.605 seconds
- Router queue mean: 0.00 ms
- Scheduler queue mean: 12.33 ms
- Compute / prefill mean: 167.83 ms
- Completion latency mean: 13.398 s
- Completion latency p95: 17.103 s

## Figures and data

- [E2E TTFT breakdown](../../figures/input2000_reuse025/three_policy_ttft_breakdown.png)
- [E2E TTFT CDF](../../figures/input2000_reuse025/three_policy_ttft_cdf.png)
- [E2E TTFT boxplot](../../figures/input2000_reuse025/three_policy_ttft_boxplot.png)
- [Scheduler queue boxplot](../../figures/input2000_reuse025/three_policy_scheduler_queue_boxplot.png)
- [Completion-latency CDF](../../figures/input2000_reuse025/three_policy_completion_cdf.png)
- [GPU-level TTFT and queue](../../figures/input2000_reuse025/gpu_ttft_and_queue.png)
- [Summary CSV](../../analysis/input2000_reuse025/summary.csv)
