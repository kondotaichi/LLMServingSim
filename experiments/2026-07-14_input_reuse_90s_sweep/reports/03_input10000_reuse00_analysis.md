# Input 10000 / reuse 0% / 90s analysis

## 結論

10000-token入力では、2000-token以下の条件と異なり、明確なKV容量逼迫とrouter capacity waitが発生した。B/Cは300件中93件（31.0%）をsecond-nearest側へredirectし、Aに対してmean E2E TTFTを6.9%、p95を27.1%改善した。

| Policy | Redirects | Mean E2E TTFT | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| A: NEAREST_KV | 0 | 19.38 s | 11.79 s | 89.49 s | 104.74 s | 110.69 s |
| B: NEAREST_MIGRATE | 93 | 18.04 s | 12.28 s | 65.27 s | 94.74 s | 102.98 s |
| C: NEAREST_MIGRATE_KV | 93 | 18.04 s | 12.28 s | 65.27 s | 94.74 s | 102.98 s |

BとCはrequest単位で完全に一致した。reuse 0%では移送できるprefix KVがなく、Cの`kv_migration_tokens`と`kv_migration_latency_ns`も0になるためである。この条件ではB/Cの差ではなく、「home GPUだけで待つA」と「second-nearestへredirect可能なB/C」の差を評価している。

## Workload

- Requests: 300
- Request send-time span: 89.605 s
- Mean arrival rate: approximately 3.33 requests/s
- Input length: exactly 10000 tokens
- Reused prefix: 0 tokens
- Output length: mean 652.51, p50 632, p95 773, max 1021 tokens
- GPUs: 10 RTX 4090, one GPU per node
- `max_num_batched_tokens`: 2048
- `max_num_seqs`: 128
- Chunked prefill and prefix caching: enabled

10000-token prefillは、他requestがいない理想条件でも最低5 batchに分割される。実際にはdecodeと他prefillが2048-token budgetを共有するため、さらに細かく分割される場合がある。また1 requestあたり約10653 tokens分のprompt + output KVを長時間保持するため、GPUメモリがほぼ100%に達した。

## E2E TTFT breakdown

| Population | n | Router queue | Scheduler queue | Prefill | Other communication | Mean E2E TTFT |
|---|---:|---:|---:|---:|---:|---:|
| A: all | 300 | 17.93 s | 95 ms | 1.350 s | 0 ms | 19.38 s |
| B: all | 300 | 16.59 s | 101 ms | 1.350 s | 0.20 ms | 18.04 s |
| B: redirected only | 93 | 33.00 s | 67 ms | 1.355 s | 0.63 ms | 34.42 s |
| B: not redirected only | 207 | 9.21 s | 117 ms | 1.347 s | 0 ms | 10.67 s |
| C: all | 300 | 16.59 s | 101 ms | 1.350 s | 0.20 ms | 18.04 s |
| C: redirected only | 93 | 33.00 s | 67 ms | 1.355 s | 0.63 ms | 34.42 s |
| C: not redirected only | 207 | 9.21 s | 117 ms | 1.347 s | 0 ms | 10.67 s |

全方策でE2E TTFTの支配項はrouter queueである。Aのmean 17.93秒、B/Cのmean 16.59秒に対し、scheduler queueは約0.1秒、prefillは約1.35秒である。したがって10000-token条件の性能差はcompute短縮ではなく、KV容量が空くまでのrouter待ちの差で説明できる。

`B/C: redirected only`のrouter queueが33秒と長いのは、redirect自体が33秒を追加したという意味ではない。home GPUとsecond-nearest GPUの両方が長時間満杯だったrequestがこの集合へ選ばれ、最終的にsecond-nearestが先に空いたためredirectされた、という条件付き集合である。

## Paired-request comparison

全300件を同じrequest IDで比較すると、B/C − AのE2E TTFT deltaは次のとおりだった。

- Mean delta: −1.341 s
- Median delta: 0 s
- B/Cが速いrequest: 131 / 300
- 5th percentile delta: −45.31 s
- 95th percentile delta: +30.90 s

ただしB/Cでredirectされた93件だけを同じIDのAと比較すると、B/Cは平均7.67秒遅く、B/Cが速いのは34 / 93件だった。一方、B/Cでredirectされなかった207件はAより平均5.39秒速く、97件で改善した。

これはredirectの効果がredirect対象だけに局所化されないためである。あるrequestを別GPUへ移すと、home GPUのKV容量、後続batch構成、各GPUのcompletion時刻、後続requestのcapacity判定が変わる。その結果、redirect対象自体が悪化する場合があっても、home GPU側に残る多数の後続requestが改善し、全体tailが短くなる。

したがって、この結果から「redirectされたrequestは平均的に速い」とは言えない。言えるのは、B/Cのrouting trajectory全体がAよりmeanとtailを改善した、ということである。

## Tail latency and completion

B/Cによる改善はmedianよりtailで大きい。

- Mean E2E TTFT: 19.38 s → 18.04 s（6.9%改善）
- p50 E2E TTFT: 11.79 s → 12.28 s（4.2%悪化）
- p95 E2E TTFT: 89.49 s → 65.27 s（27.1%改善）
- p95 completion latency: 111.79 s → 90.77 s（18.8%改善）

つまりB/Cは典型的なrequestを一様に速くする方式ではなく、極端に長くhome GPUの容量を待つrequestを減らし、分布の上側を圧縮する効果が中心である。

## Routing flow

Aは全300件がhome GPUに残った。B/Cのredirectはそれぞれ93件で、redirect集合とtarget GPUも完全に一致した。主な経路は次のとおりである。

| Home → Target | Requests |
|---|---:|
| GPU 4 → GPU 5 | 21 |
| GPU 1 → GPU 0 | 11 |
| GPU 0 → GPU 1 | 9 |
| GPU 5 → GPU 9 | 8 |
| GPU 8 → GPU 7 | 7 |
| GPU 5 → GPU 8 | 6 |
| GPU 6 → GPU 5 | 6 |

全経路は[routing flow table](../analysis/input10000_reuse00/routing_flows.md)に出力した。

## Figures and data

- [7-series E2E TTFT breakdown](../figures/input10000_reuse00/seven_series_ttft_breakdown.png)
- [7-series E2E TTFT CDF](../figures/input10000_reuse00/seven_series_ttft_cdf.png)
- [7-series E2E TTFT boxplot](../figures/input10000_reuse00/seven_series_ttft_boxplot.png)
- [7-series router queue boxplot](../figures/input10000_reuse00/seven_series_router_queue_boxplot.png)
- [7-series scheduler queue boxplot](../figures/input10000_reuse00/seven_series_scheduler_queue_boxplot.png)
- [7-series prefill boxplot](../figures/input10000_reuse00/seven_series_prefill_boxplot.png)
- [Paired TTFT delta CDF](../figures/input10000_reuse00/paired_ttft_delta_cdf.png)
- [E2E TTFT by arrival bin](../figures/input10000_reuse00/arrival_bin_ttft.png)
- [Summary CSV](../analysis/input10000_reuse00/summary.csv)
- [7-series breakdown CSV](../analysis/input10000_reuse00/seven_series_breakdown.csv)
- [Routing flows CSV](../analysis/input10000_reuse00/routing_flows.csv)

## Reproduction

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_input_reuse_90s_sweep/scripts/analyze_input10000_reuse00.py
```
