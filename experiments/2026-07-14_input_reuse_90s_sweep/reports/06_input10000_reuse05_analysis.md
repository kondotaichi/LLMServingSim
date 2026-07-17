# Input 10000 / reuse 50% / 90s analysis

## 結論

4992 of 10000 input tokens（実効49.92%）を再利用する条件では、Cがmean・p50・p95・p99・maxのすべてで最良になった。50% reuseではhandoffによるprefill短縮がKV転送費を上回り、さらにCのrouting trajectoryがrouter queueを大きく削減した。

| Policy | Redirects | Mean E2E TTFT | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| A: NEAREST_KV | 0 | 13.43 s | 6.11 s | 71.15 s | 81.90 s | 84.92 s |
| B: NEAREST_MIGRATE | 86 | 12.91 s | 7.42 s | 51.97 s | 73.59 s | 83.54 s |
| C: NEAREST_MIGRATE_KV | 91 | 10.84 s | 5.76 s | 40.67 s | 50.48 s | 60.21 s |

CはAに対してmean 19.3%、p95 42.8%、p99 38.4%を改善した。Bに対してもmean 16.1%、p95 21.7%、p99 31.4%を改善した。

## E2E TTFT breakdown

| Population | n | Router queue | Scheduler queue | KV transfer | Prefill | Mean E2E TTFT |
|---|---:|---:|---:|---:|---:|---:|
| A: all | 300 | 12.62 s | 56 ms | 0 ms | 756 ms | 13.43 s |
| B: all | 300 | 11.92 s | 65 ms | 0 ms | 924 ms | 12.91 s |
| B: redirected only | 86 | 24.66 s | 93 ms | 0 ms | 1.369 s | 26.12 s |
| B: not redirected only | 214 | 6.81 s | 54 ms | 0 ms | 744 ms | 7.61 s |
| C: all | 300 | 9.85 s | 63 ms | 160 ms | 759 ms | 10.84 s |
| C: redirected only | 91 | 18.87 s | 115 ms | 528 ms | 793 ms | 20.31 s |
| C: not redirected only | 209 | 5.93 s | 40 ms | 0 ms | 744 ms | 6.71 s |

Cの改善の最大要因は、A比でmean router queueが12.62秒から9.85秒へ2.76秒短縮したことである。平均KV transfer 160 msを含めても、このrouter queue削減が十分に大きい。

## Handoff economics

Redirect対象だけを見ると、Bはreuseを失うためmean prefill 1.369秒、Cは4992-token KVを移送してreuseを維持するためmean prefill 793 msだった。Cはprefillを約577 ms短縮し、mean KV transfer 528 msを約49 ms上回った。

reuse 25%ではprefill短縮約268 msとKV transfer約264 msがほぼ相殺された。50%では転送するKV量も増えるが、回避できるprefill計算も大きくなり、純粋なhandoff費用対効果がわずかに正になった。

## Redirect overlap and paired comparison

Bは86件、Cは91件をredirectした。

- Both redirected: 50
- B only: 36
- C only: 41
- Neither: 173

共通50件では、CはBよりmean 1.413秒、median 544 ms速く、38 / 50件で勝った。共通集合でもhandoffの利益が確認できる。

全300件のpaired deltaは次のとおりだった。

| Comparison | Mean delta | Median delta | Faster requests |
|---|---:|---:|---:|
| B − A | −513 ms | 0 ms | 113 / 300 |
| C − A | −2.592 s | 0 ms | 120 / 300 |
| C − B | −2.078 s | 0 ms | 138 / 300 |

median deltaが0なのは、多くの早い時間帯のrequestで3方策のrouting結果が同じためである。一方、高負荷時間帯の大きな改善がmeanとtailを押し下げている。

## Figures and data

- [7-series E2E TTFT breakdown](../figures/input10000_reuse05/seven_series_ttft_breakdown.png)
- [7-series E2E TTFT CDF](../figures/input10000_reuse05/seven_series_ttft_cdf.png)
- [7-series E2E TTFT boxplot](../figures/input10000_reuse05/seven_series_ttft_boxplot.png)
- [7-series router queue boxplot](../figures/input10000_reuse05/seven_series_router_queue_boxplot.png)
- [7-series scheduler queue boxplot](../figures/input10000_reuse05/seven_series_scheduler_queue_boxplot.png)
- [7-series prefill boxplot](../figures/input10000_reuse05/seven_series_prefill_boxplot.png)
- [Paired TTFT delta CDF](../figures/input10000_reuse05/paired_ttft_delta_cdf.png)
- [E2E TTFT by arrival bin](../figures/input10000_reuse05/arrival_bin_ttft.png)
- [Summary CSV](../analysis/input10000_reuse05/summary.csv)
- [7-series breakdown CSV](../analysis/input10000_reuse05/seven_series_breakdown.csv)
- [Routing flows](../analysis/input10000_reuse05/routing_flows.md)

## Reproduction

```bash
ANALYSIS_CASE=input10000_reuse05 MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/analyze_input10000_reuse00.py
```

