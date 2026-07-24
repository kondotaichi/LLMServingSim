# Interim PP1 vs PP2 analysis

This report uses only completed 300-request pairs. Positive improvement means PP2 is better.

## Coverage

- Completed comparable pairs: 6 / 10
- Missing: `input8000_reuse00` / PP2: 5 groups (missing)
- Missing: `input10000_reuse00` / PP2: 5 groups (missing)
- Missing: `input10000_reuse025` / PP1: 10 replicas (missing)
- Missing: `input10000_reuse025` / PP2: 5 groups (missing)
- Missing: `input10000_reuse05` / PP1: 10 replicas (missing)
- Missing: `input10000_reuse05` / PP2: 5 groups (missing)

## Paired result

| Workload | Mean TTFT improvement | p95 improvement | p99 improvement | Scheduler queue reduction | TPOT change | Completion change | Redirects PP1→PP2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 512 / reuse 0% | +5.6% | +8.5% | +9.5% | +39.5% | +7.1% | +7.1% | 0→0 |
| 2000 / reuse 25% | +0.3% | -17.8% | +10.6% | +17.8% | +10.4% | +10.3% | 0→0 |
| 6000 / reuse 50% | +3.2% | +12.6% | +12.7% | +28.6% | +26.8% | +26.2% | 18→0 |
| 8000 / reuse 25% | +42.8% | +64.2% | +65.6% | +2.7% | +54.9% | +47.3% | 183→35 |
| 8000 / reuse 50% | +20.4% | +35.5% | +45.0% | +25.6% | +44.5% | +42.0% | 106→12 |
| Mixed / burst | +5.1% | +5.0% | +23.6% | +40.6% | +26.8% | +25.8% | 17→0 |

## Current interpretation

- PP2 reduces mean scheduler queue in every completed pair.
- PP2 removes capacity redirects in the completed 6000-token and mixed workloads.
- Mean and tail TTFT generally improve, although the 2000-token p95 regresses.
- TPOT and end-to-end completion latency regress in every completed pair; PP2 is a TTFT/tail optimization, not an overall decode-speedup in these results.
- PP2 utilization rows represent five logical instances, not ten physical pipeline stages. Stage-level balance cannot be inferred from this CSV.
- The missing PP2 10000-token result is the strongest capacity-pressure case, so the capacity-bound conclusion remains provisional.

## Figures

- `figures/ttft_percentiles.png`
- `figures/ttft_breakdown.png`
- `figures/ttft_cdf.png`
- `figures/latency_throughput_tradeoff.png`
- `figures/logical_instance_utilization.png`
- `figures/input8000_redirect_ttft.png`
- `figures/input8000_redirect_ttft_breakdown.png`
