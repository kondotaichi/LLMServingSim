# Input 10000 / reuse 25% / 90s analysis

## 結論

2496 of 10000 input tokens（実効24.96%）を再利用する条件でも、強いKV容量逼迫とrouter waitが発生した。全体ではBが最も良く、Aに対してmean E2E TTFTを3.9%、p95を21.8%改善した。CはmeanではAとほぼ同じだが、p95を12.9%改善した一方、p99とmaxはAより悪化した。

| Policy | Redirects | Mean E2E TTFT | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| A: NEAREST_KV | 0 | 16.21 s | 8.95 s | 80.17 s | 92.30 s | 98.41 s |
| B: NEAREST_MIGRATE | 95 | 15.57 s | 9.71 s | 62.73 s | 89.98 s | 94.24 s |
| C: NEAREST_MIGRATE_KV | 99 | 16.16 s | 8.74 s | 69.80 s | 107.71 s | 108.52 s |

## Policy semantics in this condition

- Aはhome GPUが空くまで待ち、2496-token prefixをlocal reuseする。
- Bはhome GPUに入れない場合second-nearestへredirectするが、redirect対象95件はprefix reuseを失い、10000 tokensをcold prefillする。
- Cはsecond-nearestへredirectする場合、prefix KVを転送して2496-token reuseを維持する。redirect対象99件のmean KV transferは264 msだった。

結果CSVでも、Bはnon-redirect 205件で`reuse_prefix_toks=2496`、redirect 95件で`reuse_prefix_toks=0`となっている。Cは全300件で2496 tokensを維持している。

## E2E TTFT breakdown

| Population | n | Router queue | Scheduler queue | KV transfer | Prefill | Mean E2E TTFT |
|---|---:|---:|---:|---:|---:|---:|
| A: all | 300 | 15.08 s | 71 ms | 0 ms | 1.066 s | 16.21 s |
| B: all | 300 | 14.33 s | 85 ms | 0 ms | 1.158 s | 15.57 s |
| B: redirected only | 95 | 29.10 s | 87 ms | 0 ms | 1.367 s | 30.56 s |
| B: not redirected only | 205 | 7.48 s | 83 ms | 0 ms | 1.062 s | 8.63 s |
| C: all | 300 | 14.92 s | 81 ms | 87 ms | 1.073 s | 16.16 s |
| C: redirected only | 99 | 32.56 s | 112 ms | 264 ms | 1.099 s | 34.03 s |
| C: not redirected only | 201 | 6.23 s | 66 ms | 0 ms | 1.060 s | 7.36 s |

reuse 0%と同様、支配項はrouter queueである。KV handoffによるprefill短縮はredirect対象でB 1.367秒からC 1.099秒へ約268 msであり、KV transfer 264 msとほぼ相殺される。したがって、純粋なtransfer対computeの差だけではCに大きな優位性は出ない。

## BとCのredirect集合

Bは95件、Cは99件をredirectしたが、共通するのは77件だった。

- Both redirected: 77
- B only: 18
- C only: 22
- Neither: 183

共通77件では、CはBよりmean 25 ms、median 463 ms速く、57 / 77件で勝った。これはKV handoffの局所的な効果が存在することを示す。

しかし全300件ではCはBよりmean 588 ms、p95 7.07秒遅い。KV転送で到着時刻が変わることで、後続batch、KV容量解放、redirect判定、GPU queueのtrajectoryが変わり、B/Cでredirect集合自体が18件・22件ずれたためである。Cの全体悪化をKV transfer 264 msだけで説明することはできない。

## Paired-request comparison

| Comparison | Mean delta | Median delta | Faster requests |
|---|---:|---:|---:|
| B − A | −638 ms | 0 ms | 105 / 300 |
| C − A | −51 ms | 0 ms | 115 / 300 |
| C − B | +588 ms | −4 ms | 150 / 300 |

C − Bのmedianはほぼ0でCが速いrequestも半数あるが、少数の大きな悪化によってmeanとtailがBより悪くなっている。したがってこの条件でも、meanだけでなくCDFとp95/p99を見る必要がある。

## Figures and data

- [7-series E2E TTFT breakdown](../figures/input10000_reuse025/seven_series_ttft_breakdown.png)
- [7-series E2E TTFT CDF](../figures/input10000_reuse025/seven_series_ttft_cdf.png)
- [7-series E2E TTFT boxplot](../figures/input10000_reuse025/seven_series_ttft_boxplot.png)
- [7-series router queue boxplot](../figures/input10000_reuse025/seven_series_router_queue_boxplot.png)
- [7-series scheduler queue boxplot](../figures/input10000_reuse025/seven_series_scheduler_queue_boxplot.png)
- [7-series prefill boxplot](../figures/input10000_reuse025/seven_series_prefill_boxplot.png)
- [Paired TTFT delta CDF](../figures/input10000_reuse025/paired_ttft_delta_cdf.png)
- [E2E TTFT by arrival bin](../figures/input10000_reuse025/arrival_bin_ttft.png)
- [Summary CSV](../analysis/input10000_reuse025/summary.csv)
- [7-series breakdown CSV](../analysis/input10000_reuse025/seven_series_breakdown.csv)
- [Routing flows](../analysis/input10000_reuse025/routing_flows.md)

## Reproduction

```bash
ANALYSIS_CASE=input10000_reuse025 MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/analyze_input10000_reuse00.py
```

