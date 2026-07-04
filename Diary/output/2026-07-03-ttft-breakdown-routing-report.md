# max_num_batched_tokens とTTFT支配要因に関する中間レポート

日付: 2026-07-03

## 目的

異なる `max_num_batched_tokens` を持つ複数GPU環境で、リクエストの
prompt長とルーティング方式がTTFTに与える影響を調べる。

特に次の仮説を検証した。

1. 長いpromptは大きい `max_num_batched_tokens` のGPUに送ると有利。
2. 短いpromptは小さい `max_num_batched_tokens` のGPUに送っても不利にならない。
3. 複数ユーザから動的にリクエストが来る場合、TTFTの支配要因は単独リクエスト時と変わる。
4. 出力長が長い場合、TTFTにも影響するか。

## 実験環境

モデルとプロファイル:

- Model: `meta-llama/Llama-3.1-8B`
- Hardware profile: `RTXPRO6000`
- dtype: `bfloat16`
- profiler path: `profiler/perf/RTXPRO6000/meta-llama/Llama-3.1-8B/bf16`

4 GPU異種構成:

- Config: `configs/cluster/single_node_4gpu_hetero_tokens.json`
- instance 0: `max_num_batched_tokens=512`
- instance 1: `max_num_batched_tokens=1024`
- instance 2: `max_num_batched_tokens=2048`
- instance 3: `max_num_batched_tokens=4096`

注意:

- 既存profileは `engine_effective.max_num_batched_tokens=2048` で作られている。
- `max_num_batched_tokens=4096` の実行では、一部lookupが外挿になる。
- ASTRA-SimバイナリはLinux x86-64 ELFなので、Docker内で実行した。

## 実験1: 単独リクエストでの prompt長 × max_num_batched_tokens sweep

目的:

`max_num_batched_tokens` 自体が、単独リクエストのTTFTにどう効くかを確認する。

実行方法:

- Script: `scripts/sweep_ttft_budget_prompt.py`
- Output: `results/ttft_budget_prompt_sweep.csv`
- `max_num_batched_tokens`: 512, 1024, 2048, 4096
- prompt length: 128, 512, 1024, 2048, 4096, 8192
- 各条件30行出力。ただし単独・同条件では決定的なので、各条件は1回計算した値を複製。

実行コマンド:

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec pandas numpy 'protobuf>=6,<7' && \
  mkdir -p results && \
  python3 scripts/sweep_ttft_budget_prompt.py \
    --hardware RTXPRO6000 \
    --model meta-llama/Llama-3.1-8B \
    --dtype bfloat16 \
    --max-num-batched-tokens 512 1024 2048 4096 \
    --prompt-tokens 128 512 1024 2048 4096 8192 \
    --num-repeats 30 \
    --output results/ttft_budget_prompt_sweep.csv"
```

平均TTFT:

| Prompt tokens | MBT=512 | MBT=1024 | MBT=2048 | MBT=4096 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 12.60ms | 12.60ms | 12.60ms | 12.60ms |
| 512 | 23.74ms | 23.74ms | 23.74ms | 23.74ms |
| 1024 | 48.16ms | 42.43ms | 42.43ms | 42.43ms |
| 2048 | 99.12ms | 86.96ms | 82.95ms | 82.95ms |
| 4096 | 209.39ms | 182.26ms | 173.84ms | 96.97ms |
| 8192 | 463.24ms | 397.57ms | 379.39ms | 218.39ms |

chunk数:

| Prompt tokens | MBT=512 | MBT=1024 | MBT=2048 | MBT=4096 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 1 | 1 | 1 | 1 |
| 512 | 1 | 1 | 1 | 1 |
| 1024 | 2 | 1 | 1 | 1 |
| 2048 | 4 | 2 | 1 | 1 |
| 4096 | 8 | 4 | 2 | 1 |
| 8192 | 16 | 8 | 4 | 2 |

結論:

- `prompt_tokens <= max_num_batched_tokens` なら、TTFTはほぼ変わらない。
- `prompt_tokens > max_num_batched_tokens` になると、prefill chunk数が増えてTTFTが悪化する。
- 長いpromptは大きい `max_num_batched_tokens` に送る価値がある。
- 短いpromptは小さい `max_num_batched_tokens` に送っても、単独TTFTは悪化しない。
- ただし、小さい `max_num_batched_tokens` が短いpromptを単独で速くするわけではない。

## 実験2: 単独リクエストTTFTのbreakdown

データ:

- `results/ttft-single/mbt_*_prompt_*.csv`

代表値:

| Prompt | MBT | 平均TTFT | Queue | Prefill service | Prefill比率 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 128 | 512 | 13.23ms | 0.50ms | 12.73ms | 96.2% |
| 512 | 512 | 24.70ms | 0.45ms | 24.26ms | 98.2% |
| 1024 | 512 | 49.68ms | 0.50ms | 49.18ms | 99.0% |
| 2048 | 512 | 101.60ms | 0.43ms | 101.17ms | 99.6% |
| 4096 | 4096 | 101.54ms | 0.46ms | 101.08ms | 99.6% |
| 8192 | 4096 | 227.04ms | 0.44ms | 226.60ms | 99.8% |

結論:

単独リクエストでは、

```text
TTFT ≒ prefill_service
```

である。queue待ちは1ms未満で、通信遅延も入れていないため0。

したがって単独性能を見る限り、TTFT差はほぼprefill chunk処理時間の差で説明できる。

## 実験3: 動的ワークロードでのルーティング比較

ルーティング方式:

- `RR`: ラウンドロビン
- `PROMPT`: prompt長と `max_num_batched_tokens` の適合だけを見る
- `QUEUE`: queue圧だけを見る
- `HYBRID`: prompt長適合とqueue圧の両方を見る

### mixed workload

Workload:

- `workloads/hetero_prompt_mix_32.jsonl`
- 32 requests
- prompt length: 64, 128, 256, 512, 1024, 2048, 4096, 8192
- 各prompt長4件
- `output_toks=32`
- 1msごとに2リクエスト到着

結果:

| Policy | Simulation time | Req/s | Mean latency | P99 latency | Mean TTFT | P99 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 1.587s | 20.17 | 1045.68ms | 1569.94ms | 451.99ms | 1180.98ms |
| `PROMPT` | 2.034s | 15.73 | 901.24ms | 2018.67ms | 340.40ms | 1553.13ms |
| `QUEUE` | 1.690s | 18.93 | 1060.95ms | 1672.12ms | 469.25ms | 1288.71ms |
| `HYBRID` | 2.035s | 15.72 | 907.35ms | 2019.70ms | 335.23ms | 1554.17ms |

TTFT内訳:

| Policy | 平均TTFT | P99 TTFT | 平均Queue | P99 Queue | 平均Prefill | P99 Prefill | Queue比率 | Prefill比率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 451.99ms | 1180.98ms | 302.90ms | 1020.75ms | 149.09ms | 517.51ms | 67.0% | 33.0% |
| `PROMPT` | 340.40ms | 1553.13ms | 222.26ms | 1299.23ms | 118.14ms | 427.72ms | 65.3% | 34.7% |
| `QUEUE` | 469.25ms | 1288.71ms | 322.25ms | 1209.54ms | 147.00ms | 517.51ms | 68.7% | 31.3% |
| `HYBRID` | 335.23ms | 1554.17ms | 210.61ms | 1300.27ms | 124.62ms | 427.72ms | 62.8% | 37.2% |

観察:

- 動的ワークロードでは、TTFTの支配要因がprefillからqueue待ちに変わる。
- mixed workloadでは平均TTFTの約63%から69%がqueue待ち。
- `PROMPT` と `HYBRID` は平均TTFTを下げるが、長いpromptをinstance 3へ寄せるためtailは悪化する。

### long-heavy workload

Workload:

- `workloads/hetero_prompt_long_heavy_40.jsonl`
- 40 requests
- 34/40件が2048 tokens以上
- 27/40件が4096 tokens以上
- `output_toks=32`

結果:

| Policy | Simulation time | Req/s | Mean latency | P99 latency | Mean TTFT | P99 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 3.943s | 10.15 | 2383.70ms | 3926.02ms | 1376.77ms | 3543.65ms |
| `PROMPT` | 6.548s | 6.11 | 4330.78ms | 6520.36ms | 2190.56ms | 5906.88ms |
| `QUEUE` | 3.943s | 10.15 | 2383.70ms | 3926.02ms | 1376.77ms | 3543.65ms |
| `HYBRID` | 3.226s | 12.40 | 2421.87ms | 3171.92ms | 1359.15ms | 2743.37ms |

TTFT内訳:

| Policy | 平均TTFT | P99 TTFT | 平均Queue | P99 Queue | 平均Prefill | P99 Prefill | Queue比率 | Prefill比率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 1376.77ms | 3543.65ms | 1080.61ms | 3306.86ms | 296.16ms | 552.62ms | 78.5% | 21.5% |
| `PROMPT` | 2190.56ms | 5906.88ms | 1914.81ms | 5580.21ms | 275.74ms | 456.75ms | 87.4% | 12.6% |
| `QUEUE` | 1376.77ms | 3543.65ms | 1080.61ms | 3306.86ms | 296.16ms | 552.62ms | 78.5% | 21.5% |
| `HYBRID` | 1359.15ms | 2743.37ms | 1059.74ms | 2607.85ms | 299.41ms | 557.88ms | 78.0% | 22.0% |

割り当て件数:

| Policy | Instance 0 | Instance 1 | Instance 2 | Instance 3 |
| --- | ---: | ---: | ---: | ---: |
| `RR` | 10 | 10 | 10 | 10 |
| `PROMPT` | 4 | 2 | 7 | 27 |
| `QUEUE` | 10 | 10 | 10 | 10 |
| `HYBRID` | 9 | 8 | 10 | 13 |

観察:

- long-heavy workloadでは、TTFTの78%から87%がqueue待ち。
- `PROMPT` は4096/8192-token requestをinstance 3へ集中させ、queue hotspotを作った。
- その結果、平均queueが1914.81ms、P99 queueが5580.21msまで悪化した。
- `HYBRID` は8192-token requestを複数インスタンスに分散し、P99 TTFTを改善した。

## 実験4: output長を固定32から可変長にした場合

目的:

これまでの動的ワークロードは `output_toks=32` 固定だった。出力長制限を外した場合に、
TTFT breakdownが変わるかを確認する。

厳密にはシミュレータでは `output_toks` は必須なので、無限生成にはしていない。
代わりに、ShareGPT由来の可変output長を使った。

Workload:

- `workloads/hetero_prompt_mix_varout.jsonl`
- 元の mixed workload と同じ prompt/arrival
- `output_toks=32` 固定をやめ、ShareGPT由来の可変output長に置き換え
- output length:
  - min: 519
  - p50: 729
  - max: 776
  - mean: 684.5
  - sum: 21904

実行済み:

- `RR`: `results/hetero-mix-varout-RR.csv`
- `PROMPT`: `results/hetero-mix-varout-PROMPT.csv`
- `QUEUE`: `results/hetero-mix-varout-QUEUE.csv`
- `HYBRID`: `results/hetero-mix-varout-HYBRID.csv`

実行コマンド:

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  for policy in PROMPT QUEUE HYBRID; do \
    echo RUN_POLICY=\$policy; \
    PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
    python3 -m serving \
      --cluster-config configs/cluster/single_node_4gpu_hetero_tokens.json \
      --dataset workloads/hetero_prompt_mix_varout.jsonl \
      --request-routing-policy \$policy \
      --output results/hetero-mix-varout-\$policy.csv \
      --run-id hetero-mix-varout-\$policy \
      --log-level WARNING \
      --no-enable-prefix-caching; \
  done"
```

固定32との比較:

| 条件 | output平均 | output合計 | 平均TTFT | P99 TTFT | 平均Queue | 平均Prefill | 平均Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 固定32 | 32.0 | 1024 | 451.99ms | 1180.98ms | 302.90ms | 149.09ms | 1045.68ms |
| 可変output | 684.5 | 21904 | 452.13ms | 1183.32ms | 302.94ms | 149.19ms | 9376.84ms |

全ポリシー結果:

| Policy | Simulation time | Req/s | Mean TTFT | P99 TTFT | Mean queue | Mean prefill | Queue share | Mean latency | Assignment |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `RR` | 11.007s | 2.91 | 452.13ms | 1183.32ms | 302.94ms | 149.19ms | 67.0% | 9376.84ms | 8/8/8/8 |
| `PROMPT` | 13.437s | 2.38 | 340.41ms | 1553.20ms | 222.26ms | 118.15ms | 65.3% | 9331.77ms | 16/4/4/8 |
| `QUEUE` | 11.245s | 2.85 | 469.45ms | 1290.93ms | 322.37ms | 147.08ms | 68.7% | 9433.68ms | 9/7/9/7 |
| `HYBRID` | 13.438s | 2.38 | 335.23ms | 1554.26ms | 210.61ms | 124.62ms | 62.8% | 9263.37ms | 9/9/6/8 |

観察:

- このarrival patternでは、output長を大きくしてもTTFT breakdownはほぼ変わらない。
- `RR` の固定32と可変outputを比べると、平均TTFTは451.99msから452.13msでほぼ同じ。
- 一方、平均request latencyは1045.68msから9376.84msへ大きく悪化した。
- 長いoutputは、TTFTよりも `decode_after_ttft` と total request latency を支配している。
- `PROMPT` と `HYBRID` は平均TTFTは小さいが、P99 TTFTとsimulation timeは `RR` より悪い。
- これは長いdecode中もinstance 3側に負荷が残り、tail requestの完了が遅くなるため。

理由:

- mixed workloadは全リクエストが0msから15msに集中して到着する。
- ほぼ全リクエストが最初のprefill波に入るため、TTFTは初期queueとprefillで決まる。
- その後の長いdecodeはfirst token後の滞留時間を伸ばすが、TTFTにはほぼ乗らない。
- したがって、今回の可変output workloadは「長いoutputが後続リクエストのTTFT queueを増やすか」を見るには不十分。

## 全体結論

### 1. 単独リクエストではprefillがTTFTを支配する

単独リクエストではqueue待ちがほぼないため、

```text
TTFT ≒ prefill_service
```

になる。この場合、長いpromptには大きい `max_num_batched_tokens` が明確に有利。

### 2. 動的ワークロードではqueue待ちがTTFTを支配する

複数ユーザ相当の動的到着では、

```text
TTFT ≒ queue_wait + prefill_service
```

になり、queue待ちが支配的になる。mixedでは約63%から69%、long-heavyでは約78%から87%がqueue待ち。

### 3. prompt長だけを見たルーティングはhotspotを作る

`PROMPT` は単独性能の観点では自然だが、長いpromptを最大budgetのGPUへ集めるため、
動的負荷ではqueue hotspotを作る。long-heavyでは特に悪化した。

### 4. HYBRIDはlong-heavyで有効

`HYBRID` はprompt長との適合とqueue圧の両方を見るため、long-heavy workloadでは
tail TTFTと総完了時間を改善した。

### 5. 長いoutputはこのarrival patternではTTFTではなくtotal latencyに効く

出力長を固定32から平均684.5に増やすと、平均request latencyは約9倍になった。
しかしTTFTはほぼ変わらなかった。今回の到着パターンでは、全リクエストが初期に集中して到着するため、
長いdecodeはfirst token後の時間を伸ばすだけで、後続リクエストのTTFT queueにはほぼ影響しなかった。

## 次に見るべきこと

長いoutputがTTFTにも効くかを見るには、次のようなワークロードが必要。

1. 最初に長outputリクエストを流す。
2. そのdecodeがGPU上に滞留している最中に、短promptリクエストを後から到着させる。
3. 後続短promptのTTFTをbreakdownする。

この条件なら、長いdecodeが後続リクエストのqueue待ちを増やすかを直接検証できる。

候補ワークロード:

- phase 1: 0sに長outputリクエストを複数投入
- phase 2: 1sから5sの間に短promptリクエストを継続投入
- 比較:
  - output short vs output long
  - RR vs QUEUE vs HYBRID
  - 後続短promptのみのTTFT breakdown

## 実験5: output上限をShareGPT期待出力長に置く合理性

目的:

`output_toks` を一律に大きく置くのではなく、ShareGPT由来の期待出力長に近い値へ置くことが、
シミュレーション上合理的かを確認する。

注意:

このシミュレータでは `output_toks` は「上限」ではなく、実際にその数だけ生成する長さとして扱われる。
したがって、この実験では次の2条件を比較した。

- `expected`: ShareGPT由来の期待出力長を `output_toks` に設定
- `cap1024`: 全requestの `output_toks` を1024に設定した過大上限ケース

Workloads:

- `workloads/hetero_phase_sharegpt_expected_output_32.jsonl`
- `workloads/hetero_phase_output_cap1024_32.jsonl`

構成:

- 32 requests
- phase 1: 0sに長prompt requestを8件投入
- phase 2: 1.0sから3.3sまで、短/中prompt requestを100msごとに24件投入
- prompt:
  - phase 1: 2048/4096/8192中心
  - phase 2: 128/256/512/1024の繰り返し
- output:
  - `expected`: min 519, mean 684.5, max 776, total 21904
  - `cap1024`: all 1024, total 32768

実行コマンド:

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  for workload in expected cap1024; do \
    if [ \"\$workload\" = expected ]; then \
      dataset=workloads/hetero_phase_sharegpt_expected_output_32.jsonl; \
    else \
      dataset=workloads/hetero_phase_output_cap1024_32.jsonl; \
    fi; \
    for policy in RR PROMPT QUEUE HYBRID; do \
      echo RUN_WORKLOAD=\$workload RUN_POLICY=\$policy; \
      PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
      python3 -m serving \
        --cluster-config configs/cluster/single_node_4gpu_hetero_tokens.json \
        --dataset \$dataset \
        --request-routing-policy \$policy \
        --output results/hetero-phase-\$workload-\$policy.csv \
        --run-id hetero-phase-\$workload-\$policy \
        --log-level WARNING \
        --no-enable-prefix-caching; \
    done; \
  done"
```

Output CSVs:

- `results/hetero-phase-expected-RR.csv`
- `results/hetero-phase-expected-PROMPT.csv`
- `results/hetero-phase-expected-QUEUE.csv`
- `results/hetero-phase-expected-HYBRID.csv`
- `results/hetero-phase-cap1024-RR.csv`
- `results/hetero-phase-cap1024-PROMPT.csv`
- `results/hetero-phase-cap1024-QUEUE.csv`
- `results/hetero-phase-cap1024-HYBRID.csv`

全request集計:

| Workload | Policy | Output total | Simulation time | Req/s | Mean TTFT | P99 TTFT | Mean decode after TTFT | Mean latency | P99 latency | Assignment |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `expected` | `RR` | 21904 | 12.63s | 2.53 | 126.12ms | 694.66ms | 8484.51ms | 8610.63ms | 10162.54ms | 8/8/8/8 |
| `expected` | `PROMPT` | 21904 | 12.42s | 2.58 | 177.54ms | 1150.67ms | 8726.87ms | 8904.42ms | 11933.64ms | 18/6/2/6 |
| `expected` | `QUEUE` | 21904 | 12.63s | 2.53 | 126.12ms | 694.66ms | 8484.51ms | 8610.63ms | 10162.54ms | 8/8/8/8 |
| `expected` | `HYBRID` | 21904 | 12.71s | 2.52 | 147.46ms | 593.11ms | 8480.70ms | 8628.17ms | 10359.58ms | 11/9/6/6 |
| `cap1024` | `RR` | 32768 | 16.10s | 1.99 | 126.12ms | 694.66ms | 12945.68ms | 13071.80ms | 13697.55ms | 8/8/8/8 |
| `cap1024` | `PROMPT` | 32768 | 16.48s | 1.94 | 177.54ms | 1150.67ms | 13411.63ms | 13589.17ms | 16476.09ms | 18/6/2/6 |
| `cap1024` | `QUEUE` | 32768 | 16.10s | 1.99 | 126.12ms | 694.66ms | 12945.68ms | 13071.80ms | 13697.55ms | 8/8/8/8 |
| `cap1024` | `HYBRID` | 32768 | 16.57s | 1.93 | 147.46ms | 593.11ms | 12967.84ms | 13115.31ms | 14353.01ms | 11/9/6/6 |

後続短/中promptのみの集計:

| Workload | Policy | Requests | Mean TTFT | P99 TTFT | Mean queue | Mean prefill | Mean latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `expected` | `RR` | 24 | 35.77ms | 58.71ms | 6.74ms | 29.04ms | 8621.13ms |
| `expected` | `PROMPT` | 24 | 35.47ms | 65.25ms | 4.97ms | 30.50ms | 8638.68ms |
| `expected` | `QUEUE` | 24 | 35.77ms | 58.71ms | 6.74ms | 29.04ms | 8621.13ms |
| `expected` | `HYBRID` | 24 | 37.63ms | 70.17ms | 4.94ms | 32.70ms | 8561.55ms |
| `cap1024` | `RR` | 24 | 35.77ms | 58.71ms | 6.74ms | 29.04ms | 12933.53ms |
| `cap1024` | `PROMPT` | 24 | 35.47ms | 65.25ms | 4.97ms | 30.50ms | 12992.89ms |
| `cap1024` | `QUEUE` | 24 | 35.77ms | 58.71ms | 6.74ms | 29.04ms | 12933.53ms |
| `cap1024` | `HYBRID` | 24 | 37.63ms | 70.17ms | 4.94ms | 32.70ms | 12842.17ms |

観察:

- `cap1024` は `expected` より総生成トークンが約1.50倍大きい。
- その結果、simulation timeは約12.4-12.7sから約16.1-16.6sへ伸びた。
- 平均request latencyも約8.6-8.9sから約13.1-13.6sへ伸びた。
- 一方、TTFTは `expected` と `cap1024` で同じ。
- 理由は、後続requestが1.0-3.3sに到着しており、どちらの条件でもその時点では先行decodeがまだ完了していないため。
- このarrival patternでは、過大なoutput上限はTTFTではなく、decode占有時間・makespan・request latencyを過大評価する。

結論:

- シミュレータ上で `output_toks` を実際より大きく置くと、decodeを余分に実行した扱いになり、GPU占有時間とtotal latencyを過大評価する。
- したがって、出力上限を「実際に出力するであろう期待サイズ」に置くことは合理的。
- ただし、この実験だけでは「期待出力長上限が後続requestのTTFTを改善する」ことは示せない。
- TTFT差を見るには、先行requestが `expected` では完了済みだが `cap1024` ではまだdecode中、という時間帯に後続requestを投入する追加workloadが必要。
