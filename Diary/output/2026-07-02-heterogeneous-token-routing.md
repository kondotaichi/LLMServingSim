# 異なる max_num_batched_tokens を持つ4 GPUルーティング実験

日付: 2026-07-02

このメモは、4つの単一GPUインスタンスに異なる
`max_num_batched_tokens` を設定し、複数のリクエストルーティング方式を
比較したときの実行コマンド、ワークロード条件、出力CSV、結果を整理したもの。

## 実験用に追加したコードと設定

- 追加したルーティング方式:
  - `RR`: ラウンドロビンのベースライン。
  - `PROMPT`: プロンプト長を見て割り当てる方式。短いプロンプトは小さい
    `max_num_batched_tokens` のインスタンスへ、長いプロンプトは大きい
    token budget のインスタンスへ寄せる。
  - `QUEUE`: 現在のキュー圧が最も小さいインスタンスを選ぶ方式。
  - `HYBRID`: プロンプト長との相性と、現在のキュー圧の両方を見る方式。
- クラスタ設定:
  - `configs/cluster/single_node_4gpu_hetero_tokens.json`
  - `RTXPRO6000` 上の `meta-llama/Llama-3.1-8B` を4つの独立インスタンスとして扱う。
  - 各インスタンスは `tp_size=1`, `num_npus=1`。
  - インスタンスごとの token budget:
    - instance 0: `max_num_batched_tokens=512`
    - instance 1: `max_num_batched_tokens=1024`
    - instance 2: `max_num_batched_tokens=2048`
    - instance 3: `max_num_batched_tokens=4096`

注意: 手元にある Llama-3.1-8B のプロファイルは
`engine_effective.max_num_batched_tokens=2048` で作られている。そのため
4096-token のインスタンスを使う実行では警告が出て、profile lookup は外挿になる。

## 実行環境の注意

この checkout にある ASTRA-Sim バイナリは Linux x86-64 ELF なので、
macOSホスト上では直接実行できない。今回のシミュレーションは既存の
`astrasim/tutorial-micro2024` Dockerイメージ内で実行した。

コンテナ内では、実行時に以下のPythonパッケージを追加した。

```bash
pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7'
```

`protobuf>=6,<7` が必要だった理由は、checkout済みの Chakra protobuf 生成コードが
protobuf 6.x で生成されている一方、ベースコンテナ内の protobuf runtime が5.xだったため。

## スモークテスト

本実験の前に、ASTRA-Sim起動、Chakra変換、profile読み込み、CSV出力が動くことを
確認するために使ったコマンド。

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
  python3 -m serving \
    --cluster-config configs/cluster/single_node_4gpu_hetero_tokens.json \
    --dataset workloads/hetero_prompt_mix_32.jsonl \
    --num-reqs 4 \
    --request-routing-policy RR \
    --output results/hetero-routing-smoke-rr.csv \
    --run-id hetero-routing-smoke-rr-docker \
    --log-level WARNING \
    --no-enable-prefix-caching"
```

出力CSV:

- `results/hetero-routing-smoke-rr.csv`

## 実験1: 短いプロンプトから長いプロンプトまで均等に混ぜたワークロード

ワークロード:

- `workloads/hetero_prompt_mix_32.jsonl`
- 32 requests
- すべて `output_toks=32`
- 到着時刻の範囲: `0ns` から `15,000,000ns`
- 1msごとに2リクエスト到着
- 入力長: `64, 128, 256, 512, 1024, 2048, 4096, 8192`
- 各入力長が4回ずつ出現

実行コマンド:

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  for policy in RR PROMPT QUEUE HYBRID; do \
    echo RUN_POLICY=\$policy; \
    PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
    python3 -m serving \
      --cluster-config configs/cluster/single_node_4gpu_hetero_tokens.json \
      --dataset workloads/hetero_prompt_mix_32.jsonl \
      --request-routing-policy \$policy \
      --output results/hetero-routing-\$policy.csv \
      --run-id hetero-routing-\$policy \
      --log-level WARNING \
      --no-enable-prefix-caching; \
  done"
```

出力CSV:

- `results/hetero-routing-RR.csv`
- `results/hetero-routing-PROMPT.csv`
- `results/hetero-routing-QUEUE.csv`
- `results/hetero-routing-HYBRID.csv`

結果:

| Policy | シミュレーション時間 | Req/s | 平均Latency | P99 Latency | 平均TTFT | P99 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 1.587s | 20.17 | 1045.68ms | 1569.94ms | 451.99ms | 1180.98ms |
| `PROMPT` | 2.034s | 15.73 | 901.24ms | 2018.67ms | 340.40ms | 1553.13ms |
| `QUEUE` | 1.690s | 18.93 | 1060.95ms | 1672.12ms | 469.25ms | 1288.71ms |
| `HYBRID` | 2.035s | 15.72 | 907.35ms | 2019.70ms | 335.23ms | 1554.17ms |

観察:

- 総完了時間とリクエストスループットでは `RR` が最良だった。
- `PROMPT` と `HYBRID` は平均TTFTを改善したが、長いプロンプトが最大token budgetの
  インスタンスに集中し、tail latency と総完了時間が悪化した。

## 実験2: 長いプロンプトを多めにしたワークロード

ワークロード:

- `workloads/hetero_prompt_long_heavy_40.jsonl`
- 40 requests
- すべて `output_toks=32`
- 到着時刻の範囲: `0ns` から `19,000,000ns`
- 1msごとに2リクエスト到着
- 入力長の分布:
  - 128: 1 request
  - 256: 1 request
  - 512: 2 requests
  - 1024: 2 requests
  - 2048: 7 requests
  - 4096: 12 requests
  - 8192: 15 requests
- 40件中34件が2048 input tokens以上。
- 40件中27件が4096 input tokens以上。

実行コマンド:

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  for policy in RR PROMPT QUEUE HYBRID; do \
    echo RUN_POLICY=\$policy; \
    PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
    python3 -m serving \
      --cluster-config configs/cluster/single_node_4gpu_hetero_tokens.json \
      --dataset workloads/hetero_prompt_long_heavy_40.jsonl \
      --request-routing-policy \$policy \
      --output results/hetero-long-heavy-\$policy.csv \
      --run-id hetero-long-heavy-\$policy \
      --log-level WARNING \
      --no-enable-prefix-caching; \
  done"
```

出力CSV:

- `results/hetero-long-heavy-RR.csv`
- `results/hetero-long-heavy-PROMPT.csv`
- `results/hetero-long-heavy-QUEUE.csv`
- `results/hetero-long-heavy-HYBRID.csv`

結果:

| Policy | シミュレーション時間 | Req/s | 平均Latency | P99 Latency | 平均TTFT | P99 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 3.943s | 10.15 | 2383.70ms | 3926.02ms | 1376.77ms | 3543.65ms |
| `PROMPT` | 6.548s | 6.11 | 4330.78ms | 6520.36ms | 2190.56ms | 5906.88ms |
| `QUEUE` | 3.943s | 10.15 | 2383.70ms | 3926.02ms | 1376.77ms | 3543.65ms |
| `HYBRID` | 3.226s | 12.40 | 2421.87ms | 3171.92ms | 1359.15ms | 2743.37ms |

割り当て件数:

| Policy | Instance 0 | Instance 1 | Instance 2 | Instance 3 |
| --- | ---: | ---: | ---: | ---: |
| `RR` | 10 | 10 | 10 | 10 |
| `PROMPT` | 4 | 2 | 7 | 27 |
| `QUEUE` | 10 | 10 | 10 | 10 |
| `HYBRID` | 9 | 8 | 10 | 13 |

観察:

- 長いプロンプトが多いワークロードでは `HYBRID` が最良だった。
- `PROMPT` はほぼすべての4096/8192-tokenリクエストを4096-token-budgetの
  インスタンスへ送ってしまい、深刻なキューホットスポットを作った。
- `HYBRID` はプロンプト長を考慮しつつ、8192-tokenリクエストも複数インスタンスへ
  分散できたため、tail latency と総完了時間を改善した。

## CSV集計用ヘルパー

上の表は、per-request CSVから以下のスクリプトで集計した。

```bash
python3 - <<'PY'
import csv, statistics, math, collections
from pathlib import Path

prefix = "results/hetero-long-heavy"
policies = ["RR", "PROMPT", "QUEUE", "HYBRID"]

for policy in policies:
    rows = list(csv.DictReader(Path(f"{prefix}-{policy}.csv").open()))

    def vals(key):
        return [float(row[key]) / 1e6 for row in rows]

    def pct(values, q):
        values = sorted(values)
        pos = (len(values) - 1) * q
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return values[lo]
        return values[lo] * (hi - pos) + values[hi] * (pos - lo)

    lat = vals("latency")
    ttft = vals("TTFT")
    end_s = max(int(row["end_time"]) for row in rows) / 1e9
    assignments = collections.Counter(int(row["instance id"]) for row in rows)

    print(policy)
    print("  n:", len(rows))
    print("  simulation_s:", round(end_s, 3))
    print("  req_s:", round(len(rows) / end_s, 2))
    print("  mean_latency_ms:", round(statistics.mean(lat), 2))
    print("  p99_latency_ms:", round(pct(lat, 0.99), 2))
    print("  mean_ttft_ms:", round(statistics.mean(ttft), 2))
    print("  p99_ttft_ms:", round(pct(ttft, 0.99), 2))
    print("  assignments:", dict(sorted(assignments.items())))
PY
```

## 動的ワークロードでのTTFT内訳

単独リクエストの実験ではTTFTのほとんどがprefill serviceだったが、
複数ユーザ相当の動的ワークロードでは支配要因が変わる。以下では
`TTFT` を主に `queueing_before_ttft_ns` と `prefill_service_ns` に分けて集計した。
この実験では地理通信を入れていないため `communication_latency_ns` は0。

### 実験1: mixed workload の内訳

breakdown列が入るように、mixed workload は現在のコードで再実行した。

出力CSV:

- `results/hetero-routing-breakdown-RR.csv`
- `results/hetero-routing-breakdown-PROMPT.csv`
- `results/hetero-routing-breakdown-QUEUE.csv`
- `results/hetero-routing-breakdown-HYBRID.csv`

| Policy | 平均TTFT | P99 TTFT | 平均Queue | P99 Queue | 平均Prefill | P99 Prefill | Queue比率 | Prefill比率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 451.99ms | 1180.98ms | 302.90ms | 1020.75ms | 149.09ms | 517.51ms | 67.0% | 33.0% |
| `PROMPT` | 340.40ms | 1553.13ms | 222.26ms | 1299.23ms | 118.14ms | 427.72ms | 65.3% | 34.7% |
| `QUEUE` | 469.25ms | 1288.71ms | 322.25ms | 1209.54ms | 147.00ms | 517.51ms | 68.7% | 31.3% |
| `HYBRID` | 335.23ms | 1554.17ms | 210.61ms | 1300.27ms | 124.62ms | 427.72ms | 62.8% | 37.2% |

観察:

- mixed workload でもTTFTの最大要因はqueue待ち。
- 単独リクエストではprefillが支配的だったが、動的到着ではqueueが60%超を占める。
- `PROMPT` / `HYBRID` は平均TTFTを下げる一方、4096/8192-tokenリクエストを
  instance 3 に寄せるため、P99 queueが大きくなりtail TTFTは悪化する。

### 実験2: long-heavy workload の内訳

出力CSV:

- `results/hetero-long-heavy-RR.csv`
- `results/hetero-long-heavy-PROMPT.csv`
- `results/hetero-long-heavy-QUEUE.csv`
- `results/hetero-long-heavy-HYBRID.csv`

| Policy | 平均TTFT | P99 TTFT | 平均Queue | P99 Queue | 平均Prefill | P99 Prefill | Queue比率 | Prefill比率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `RR` | 1376.77ms | 3543.65ms | 1080.61ms | 3306.86ms | 296.16ms | 552.62ms | 78.5% | 21.5% |
| `PROMPT` | 2190.56ms | 5906.88ms | 1914.81ms | 5580.21ms | 275.74ms | 456.75ms | 87.4% | 12.6% |
| `QUEUE` | 1376.77ms | 3543.65ms | 1080.61ms | 3306.86ms | 296.16ms | 552.62ms | 78.5% | 21.5% |
| `HYBRID` | 1359.15ms | 2743.37ms | 1059.74ms | 2607.85ms | 299.41ms | 557.88ms | 78.0% | 22.0% |

観察:

- long-heavy workload ではqueue待ちがさらに支配的で、平均TTFTの78%から87%を占める。
- `PROMPT` は4096/8192-token requestをinstance 3へ集中させたため、平均queueが
  1914.81ms、P99 queueが5580.21msまで悪化した。
- `HYBRID` はqueue圧も見て8192-token requestを分散したため、P99 queueを2607.85msまで
  下げ、P99 TTFTも2743.37msまで改善した。

### 結論

単独リクエストではTTFTはほぼprefill serviceで決まる。一方、複数ユーザから
さまざまなプロンプト長が動的に送られる状況では、TTFTの支配要因はqueue待ちになる。

したがってルーティングで重要なのは、単に「長いpromptを大きい
`max_num_batched_tokens` に送る」ことではない。長いpromptを同じ大きいbudgetの
インスタンスへ集めすぎるとqueue hotspotが発生し、prefill短縮分を上回ってTTFTが悪化する。

動的ワークロードでは次のように考えるのが妥当。

- 単独性能: 長いpromptには大きい `max_num_batched_tokens` が有利。
- 動的性能: TTFTはqueue待ちに支配されるため、prompt長の適合だけでなくqueue圧の分散が必要。
- `PROMPT` は単独性能の仮説を強く使いすぎてhotspotを作る。
- `HYBRID` はprompt長適合とqueue圧分散の両方を見るため、long-heavyではtail TTFTを改善できた。
