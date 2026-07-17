# Input 10000 / 90s: reuse 0% vs 25%

## Summary

| Reuse | Policy | Redirects | Mean E2E TTFT | p95 | Mean router queue | Mean prefill |
|---|---|---:|---:|---:|---:|---:|
| 0% | A | 0 | 19.38 s | 89.49 s | 17.93 s | 1.350 s |
| 0% | B | 93 | 18.04 s | 65.27 s | 16.59 s | 1.350 s |
| 0% | C | 93 | 18.04 s | 65.27 s | 16.59 s | 1.350 s |
| 25% | A | 0 | 16.21 s | 80.17 s | 15.08 s | 1.066 s |
| 25% | B | 95 | 15.57 s | 62.73 s | 14.33 s | 1.158 s |
| 25% | C | 99 | 16.16 s | 69.80 s | 14.92 s | 1.073 s |

## Effect of reuse

0%から25% reuseにすると、Aではmean E2E TTFTが16.3%、p95が10.4%改善した。Mean prefillは21.0%短縮しているが、mean router queueも15.9%短縮している。これはprefillが速く終わることでKV容量が早く解放され、後続requestのrouter capacity waitも短くなるためである。

Bではmean E2E TTFTが13.7%、p95が3.9%改善した。Bのredirect対象はreuseを失うため、Aほどreuseの恩恵を受けない。実際、25%条件のB redirected-only prefillは1.367秒で、0%条件の1.355秒とほぼ同じである。

Cではmean E2E TTFTが10.4%改善したが、p95は65.27秒から69.80秒へ6.9%悪化した。Reuseによるcompute削減があっても、KV transferによるarrival timingと後続routing trajectoryの変化によってtailが悪化し得る。

## Interpretation

Input 10000では、reuseの直接効果であるprefill短縮よりrouter queueの絶対値が一桁以上大きい。したがって最終性能は、各requestが何tokens計算するかだけでなく、短縮されたprefillがいつKV容量を解放し、その後どのrequestがどのGPUへadmitされるかに強く依存する。

この結果では、25% reuseでもBがmean・p95とも最良だった。CのKV handoffは共通redirect集合では局所的に有効だが、全体trajectoryを含めるとBを上回らなかった。

## Figures and data

- [Mean and p95 comparison](../figures/input10000_comparison/ttft_mean_p95_comparison.png)
- [Breakdown comparison](../figures/input10000_comparison/breakdown_comparison.png)
- [Redirect-count comparison](../figures/input10000_comparison/redirect_count_comparison.png)
- [Comparison CSV](../analysis/input10000_reuse_comparison.csv)
- [Reuse 0% detailed report](03_input10000_reuse00_analysis.md)
- [Reuse 25% detailed report](04_input10000_reuse025_analysis.md)

## Reproduction

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/compare_input10000_reuse.py
```
