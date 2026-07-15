# Full input/reuse sweep analysis: 90-second workloads

## Summary

All six workloads completed with 300 requests per policy. No redirects occurred, and A/B/C are request-level identical within every workload.

| Workload | Mean E2E TTFT | p50 | p95 | Router queue | Scheduler queue | Prefill |
|---|---:|---:|---:|---:|---:|---:|
| Input 512 / reuse 0% | 65.85 ms | 65.61 ms | 76.13 ms | 0.00 ms | 8.34 ms | 57.51 ms |
| Input 512 / reuse 25% | 53.45 ms | 53.73 ms | 63.01 ms | 0.00 ms | 8.60 ms | 44.86 ms |
| Input 512 / reuse 50% | 40.71 ms | 40.65 ms | 49.83 ms | 0.00 ms | 8.22 ms | 32.49 ms |
| Input 2000 / reuse 0% | 233.91 ms | 226.74 ms | 298.47 ms | 0.00 ms | 16.51 ms | 217.40 ms |
| Input 2000 / reuse 25% | 180.16 ms | 176.38 ms | 198.50 ms | 0.00 ms | 12.33 ms | 167.83 ms |
| Input 2000 / reuse 50% | 124.03 ms | 123.72 ms | 134.83 ms | 0.00 ms | 10.11 ms | 113.93 ms |

## Main findings

- Routing policy does not affect these workloads because every request is admitted to its home GPU without router capacity waiting.
- Prefix reuse reduces compute/prefill and E2E TTFT. Scheduler queue changes are secondary in comparison.
- At input 512, 50% reuse reduces mean E2E TTFT from 65.85 ms to 40.71 ms (38.2%) and mean prefill from 57.51 ms to 32.49 ms (43.5%).
- At input 2000, the block-rounded 49.6% reuse reduces mean E2E TTFT from 233.91 ms to 124.03 ms (47.0%) and mean prefill from 217.40 ms to 113.93 ms (47.6%).
- The 2000-token workloads have higher scheduler-queue tails because a prefill that nearly fills the 2048-token batch budget is more likely to be split when decode or other prefill requests share the batch.
- Recorded communication latency is zero in all result CSVs, so the reported E2E TTFT does not include an effective network contribution.
- TTFT is prefill-dominated and completion latency is decode-dominated for every request.

## Cross-workload figures

- [Mean and p95 E2E TTFT](../figures/workload_ttft_comparison.png)
- [Reuse sweep: TTFT and prefill](../figures/reuse_sweep_ttft_and_prefill.png)
- [Combined summary CSV](../analysis/completed_cases_summary.csv)

## Case reports

- [Input 512 / reuse 0% / 90s](cases/input512_reuse00.md)
- [Input 512 / reuse 25% / 90s](cases/input512_reuse025.md)
- [Input 512 / reuse 50% / 90s](cases/input512_reuse05.md)
- [Input 2000 / reuse 0% / 90s](cases/input2000_reuse00.md)
- [Input 2000 / reuse 25% / 90s](cases/input2000_reuse025.md)
- [Input 2000 / reuse 50% / 90s](cases/input2000_reuse05.md)

## Reproduction

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/analyze_completed_cases.py
```
