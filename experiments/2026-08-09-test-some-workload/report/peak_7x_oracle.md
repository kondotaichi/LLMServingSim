# Peak 7x perfect-prewarm oracle

## Definition

This experiment reuses the PP=1 `peak_7x_seed1` workload and configuration
behind `2026-08-01_hongo_workload/figures/peak_7x_ttft_breakdown.svg`.
The normal arm is `redirect_kv_nopp`. In the oracle arm, every redirect that
would use `migrate_kv` is assumed to have the complete reusable prefix already
resident at the selected destination. It therefore pays zero request-visible
KV-transfer latency. Routing and scheduling are still simulated normally, so
downstream queueing feedback is included.

This is an intentionally optimistic upper bound. It assumes perfect knowledge
of whether a request will redirect, its destination and reusable prefix, and it
does not charge background-transfer time, bandwidth contention, expiry, or
cache-pollution cost.

## Result

| Metric | Normal KV redirect | Perfect-prewarm oracle | Oracle gain |
|---|---:|---:|---:|
| Requests | 300 | 300 | - |
| Redirects | 101 | 106 | +5 |
| Mean TTFT | 1754.9 ms | 1704.0 ms | 51.0 ms (2.90%) |
| p50 TTFT | 1195.3 ms | 1194.4 ms | 0.9 ms (0.08%) |
| p95 TTFT | 5284.5 ms | 5161.2 ms | 123.4 ms (2.33%) |
| p99 TTFT | 5982.6 ms | 5844.7 ms | 138.0 ms (2.31%) |
| Maximum TTFT | 6434.4 ms | 6026.0 ms | 408.4 ms (6.35%) |
| Mean KV-transfer attribution | 66.1 ms | 0 ms | 66.1 ms |

The current non-oracle proactive policy produced only 4 hits and a mean TTFT
of 1755.7 ms. It did not improve on the 1754.9 ms normal arm in this run.

Request-ID-paired comparison shows that 99 requests improve, 31 regress, and
170 are unchanged under the oracle. Among the 101 requests redirected in the
normal arm, the mean improvement is 182.5 ms and the median improvement is
137.9 ms. All 101 remain redirected under the oracle; five additional requests
redirect because eliminating transfer delays changes subsequent cluster state.

## Interpretation

Even with every speculative placement succeeding for free, the system-wide
mean and tail improvement is about 2-3%, while the median is unchanged. The
oracle removes 66.1 ms of average KV-transfer attribution, but the net mean
gain is only 51.0 ms because the changed timing also increases router and
scheduler queueing by about 20.4 ms in aggregate, partly offset by about 5.3 ms
less prefill service.

The upper bound is meaningful for redirected requests, but modest for the full
workload. Any realizable predictor must additionally pay or model speculative
transfer bandwidth and cache pollution, so it cannot be expected to reach this
bound. On this workload, proactive prewarm is therefore better treated as a
limited optimization for redirected requests than as a primary system-wide
method.

## Artifacts

- Normal result: `2026-08-01_hongo_workload/results/peak_7x_seed1_3_redirect_kv_nopp/requests.csv`
- Existing proactive result: `2026-08-01_hongo_workload/results/peak_7x_seed1_6_redirect_kv_nopp_c/requests.csv`
- Oracle result: `2026-08-09-test-some-workload/results/peak_7x_oracle/requests.csv`
- Oracle runner: `2026-08-09-test-some-workload/scripts/run_perfect_prewarm_oracle.py`
