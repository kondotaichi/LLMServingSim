# Input 512 / KV reuse 0% / 60s result

## Executive summary

入力512 tokens、KV reuse 0%、300 requestsを約60秒で投入した条件では、A/B/Cの結果はrequest単位で完全に一致した。

| Policy | Requests | Redirects | Mean E2E TTFT | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: NEAREST_KV | 300 | 0 | 68.44 ms | 68.54 ms | 81.12 ms | 87.27 ms | 103.24 ms |
| B: NEAREST_MIGRATE | 300 | 0 | 68.44 ms | 68.54 ms | 81.12 ms | 87.27 ms | 103.24 ms |
| C: NEAREST_MIGRATE_KV | 300 | 0 | 68.44 ms | 68.54 ms | 81.12 ms | 87.27 ms | 103.24 ms |

この条件ではrouter capacity waitが一度も発生せず、全requestがhome GPUへそのまま投入された。このため、別GPUへ移すBとKVをhandoffするCの分岐が実行されず、3方策の性能差は生じない。

## Workload configuration

- Requests: 300
- Request send-time span: 59.737 s
- Mean arrival rate: approximately 5 requests/s
- Input length: exactly 512 tokens
- Reused prefix: exactly 0 tokens (0%)
- Output length: mean 652.51, p50 632, p95 773, max 1021 tokens
- Model: `meta-llama/Llama-3.1-8B`
- Cluster: 10 nodes, one RTX 4090 GPU per node
- `max_num_batched_tokens`: 2048
- `max_num_seqs`: 128
- Prefix caching: enabled, but this workload has no reusable prefix
- Chunked prefill: enabled

## E2E TTFT breakdown

| Component | Mean | Share of E2E TTFT |
|---|---:|---:|
| Router queue | 0.00 ms | 0.0% |
| Scheduler queue | 9.08 ms | 13.3% |
| KV transfer | 0.00 ms | 0.0% |
| Compute / prefill | 59.36 ms | 86.7% |
| Recorded RTT / other communication | 0.00 ms | 0.0% |
| E2E TTFT | 68.44 ms | 100.0% |

全300件の`ttft_bottleneck`は`prefill`だった。512-token promptは2048-token batch budget以内に収まり、6000-token workloadのように1 requestのprefillを最低3 batchへ分割する必要がない。このことが短いTTFTと小さいscheduler queueにつながっている。

Scheduler queueは平均9.08 ms、p50 8.92 ms、p95 17.49 ms、最大36.05 msだった。299件で正のscheduler waitが記録されているが、容量待ちではなく、到着時に各GPUで実行中だったbatchが完了して次のbatchへ参加するまでの短い待ちである。

CSV上の`uplink_latency_ns`、`downlink_latency_ns`、`communication_latency_ns`は全件0だった。そのため、この結果の`e2e_ttft_ns`には実効的なnetwork latencyが加算されていない。metadataにはanalytical networkと記録されているため、他実験とnetwork込みの絶対値を比較する場合は、この差を確認する必要がある。

## Why no redirect occurred

この実験では、A/B/Cのすべてで次が成立した。

- `rerouted = 0` for all 300 requests
- `redirect_capacity_reason` is empty for all 300 requests
- Final `gpu_id` is identical across the three policies
- E2E TTFT、scheduler queue、prefill time、completion latencyがrequest単位で一致

入力512 tokensでは、6000-token workloadと比べて1 requestあたりのprompt KV予約量が小さい。加えてreuse 0%なので、Cが転送できる既存prefix KVも存在しない。したがって、今回の条件はredirect/handoff方式の有効性を測る条件ではなく、capacity pressureがない場合に3方策が同じ挙動へ収束するcontrol caseと解釈するのが適切である。

## Completion latency

| Metric | Value |
|---|---:|
| Mean completion latency | 12.220 s |
| p50 completion latency | 11.921 s |
| p95 completion latency | 14.972 s |
| p99 completion latency | 15.827 s |
| Max completion latency | 18.886 s |

TTFTは約68 msと短い一方、completion latencyは平均12.22 sだった。`decode_after_ttft_ns`は平均12.15 sで、全300件の`total_latency_bottleneck`は`decode`だった。これは平均652.5 output tokensを逐次生成する時間が、512-token prefillよりはるかに長いためである。

## GPU-level observation

Home GPUごとのmean E2E TTFTは65.13–73.26 msの範囲だった。最もrequest数が多いGPU 4は49件を処理し、mean E2E TTFT 73.26 ms、mean scheduler queue 10.13 msだった。最も少ないGPU 2は22件を処理し、mean E2E TTFT 65.13 ms、mean scheduler queue 7.72 msだった。軽い負荷差は見えるが、redirectが必要になる容量逼迫には至っていない。

## Figures and data

- [E2E TTFT breakdown](../figures/input512_reuse00/three_policy_ttft_breakdown.png)
- [E2E TTFT CDF](../figures/input512_reuse00/three_policy_ttft_cdf.png)
- [E2E TTFT boxplot](../figures/input512_reuse00/three_policy_ttft_boxplot.png)
- [Scheduler queue boxplot](../figures/input512_reuse00/three_policy_scheduler_queue_boxplot.png)
- [Completion-latency CDF](../figures/input512_reuse00/three_policy_completion_cdf.png)
- [GPU-level TTFT and queue](../figures/input512_reuse00/gpu_ttft_and_queue.png)
- [Summary CSV](../analysis/input512_reuse00/summary.csv)
- [Summary JSON](../analysis/input512_reuse00/summary.json)

The figures can be regenerated with:

```bash
python3 experiments/2026-07-14_input_reuse_sweep_60s/scripts/analyze_input512_reuse00.py
```

