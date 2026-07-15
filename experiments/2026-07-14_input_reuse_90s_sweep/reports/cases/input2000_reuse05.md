# Input 2000 / reuse 50% / 90s

## Result

| Policy | Requests | Redirects | Mean E2E TTFT | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| NEAREST_KV | 300 | 0 | 124.03 ms | 123.72 ms | 134.83 ms | 188.48 ms |
| NEAREST_MIGRATE | 300 | 0 | 124.03 ms | 123.72 ms | 134.83 ms | 188.48 ms |
| NEAREST_MIGRATE_KV | 300 | 0 | 124.03 ms | 123.72 ms | 134.83 ms | 188.48 ms |

All three policies are request-level identical. No router capacity rejection, redirect, or KV handoff occurred.

## Workload and breakdown

- Input tokens: 2000
- Reused prefix: 992 tokens (49.6% effective; 50% nominal)
- Requests: 300 over 89.605 seconds
- Router queue mean: 0.00 ms
- Scheduler queue mean: 10.11 ms
- Compute / prefill mean: 113.93 ms
- Completion latency mean: 13.068 s
- Completion latency p95: 16.444 s

## Figures and data

- [E2E TTFT breakdown](../../figures/input2000_reuse05/three_policy_ttft_breakdown.png)
- [E2E TTFT CDF](../../figures/input2000_reuse05/three_policy_ttft_cdf.png)
- [E2E TTFT boxplot](../../figures/input2000_reuse05/three_policy_ttft_boxplot.png)
- [Scheduler queue boxplot](../../figures/input2000_reuse05/three_policy_scheduler_queue_boxplot.png)
- [Completion-latency CDF](../../figures/input2000_reuse05/three_policy_completion_cdf.png)
- [GPU-level TTFT and queue](../../figures/input2000_reuse05/gpu_ttft_and_queue.png)
- [Summary CSV](../../analysis/input2000_reuse05/summary.csv)
