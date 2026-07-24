# O(N) full-scan timing result

The benchmark scanned 10 through 100,000 candidate GPUs and selected the minimum
capacity pressure using `Router._capacity_snapshot()`. Each point is the median of
30 to 2,000 repetitions. The fit uses measurements with at least 100 candidates.

## Linear fits

| Active requests per GPU | Fit (nanoseconds) | R-squared | 10,000-GPU prediction |
|---:|---:|---:|---:|
| 0 | `-85,648 + 1,898.2 N` | 0.999997 | 18.896 ms |
| 32 | `338,557 + 13,816.0 N` | 0.999999 | 138.498 ms |

The measured 10,000-GPU medians were 18.643 ms and 138.945 ms, respectively, so
10,000 GPUs are inside the measured range rather than a distant extrapolation.

For 300,000 requests, a single serialized router would spend approximately 1.57
hours on scans in the idle-snapshot case or 11.54 hours with 32 active requests per
GPU if every request performed a full scan. At 1,667 requests/s, sustaining the input
rate would require roughly 32 or 231 CPU cores devoted only to these scans, before
coordination and state-distribution overhead.

## Scope

This is host wall-clock time, not simulated latency. It measures the intended O(N)
minimum scan and excludes the formula policy's repeated global-feature construction,
which currently introduces a separate O(N^2) cost. Active-request count changes the
coefficient because `_capacity_snapshot()` walks each candidate GPU's admitted
requests to estimate projected KV usage.
