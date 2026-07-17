# Input 10000 / 90s: full reuse comparison

## Summary

| Reuse | A mean / p95 | B mean / p95 | C mean / p95 |
|---|---:|---:|---:|
| 0% | 19.38 / 89.49 s | 18.04 / 65.27 s | 18.04 / 65.27 s |
| 25% | 16.21 / 80.17 s | 15.57 / 62.73 s | 16.16 / 69.80 s |
| 50% | 13.43 / 71.15 s | 12.91 / 51.97 s | 10.84 / 40.67 s |

## Main findings

1. Reuse率を上げると、すべての方策でprefillだけでなくrouter queueも短くなる。Prefill完了とKV解放が早まり、後続requestのcapacity waitが減るためである。
2. Reuse 0%ではB/Cは完全に同一であり、KV handoffの効果は存在しない。
3. Reuse 25%ではBが最良だった。Cの局所的なprefill短縮はKV transferとほぼ相殺され、routing trajectoryの差によってCのtailがBより悪化した。
4. Reuse 50%ではCが明確に最良になった。Redirect対象のprefill短縮約577 msがKV transfer約528 msを上回り、C全体のrouter queueもBより約2.07秒短かった。
5. KV handoffの有効性にはreuse率の閾値があり、このworkloadでは25%では不十分、50%では有効だった。ただし閾値はnetwork帯域、GPU compute、KV容量、arrival rateによって変わる。

## C relative to B

| Reuse | C − B mean E2E TTFT | C − B p95 | Interpretation |
|---|---:|---:|---|
| 0% | 0 s | 0 s | No reusable KV |
| 25% | +0.588 s | +7.07 s | Handoff cost and routing trajectory outweigh benefit |
| 50% | −2.078 s | −11.30 s | Handoff and earlier KV release improve mean and tail |

## Figures and data

- [Mean and p95 comparison](../figures/input10000_comparison/ttft_mean_p95_comparison.png)
- [Breakdown comparison](../figures/input10000_comparison/breakdown_comparison.png)
- [Redirect-count comparison](../figures/input10000_comparison/redirect_count_comparison.png)
- [Comparison CSV](../analysis/input10000_reuse_comparison.csv)
- [Reuse 0% detailed report](03_input10000_reuse00_analysis.md)
- [Reuse 25% detailed report](04_input10000_reuse025_analysis.md)
- [Reuse 50% detailed report](06_input10000_reuse05_analysis.md)

## Reproduction

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/compare_input10000_reuse.py
```
