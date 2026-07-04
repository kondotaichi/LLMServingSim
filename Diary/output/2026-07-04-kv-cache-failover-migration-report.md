# KV cache failover migration experiment

Date: 2026-07-04

このメモは、GPU Aに存在するprompt prefixのKV cacheを、GPU A障害時にGPU Bへ移送して使う場合と、GPU Bでゼロからprefillし直す場合を比較するために追加した実装と、スモーク実験の結果を記録する。

## 目的

想定するケース:

1. GPU Aに、ユーザXのprompt `i` のKV cacheが存在する。
2. ユーザXから、prompt `i` に少し情報を追加したprompt `j` が来る。
3. GPU Aがdownしているため、GPU Bが処理を引き継ぐ。
4. 比較する方式:
   - `cold`: GPU Bでprompt `j` をゼロからprefillする。
   - `migrate_kv`: prompt `i` のKV cacheをGPU Bへ移送し、追加分だけprefillする。

GPU間条件:

- GPU間スループット: 100Gbps
- GPU間距離: 10km
- 伝搬遅延: 5ns/m

## 実装方針

このシミュレータは実際のKV tensorを保持していない。KV cacheは、prefix token列に対応するRadixCache metadataと、token数から計算されるKV byte量として扱われる。

そのため、KV移送は以下の抽象化で実装した。

```text
1. target GPU BのNPU prefix cacheへ、prompt i のprefix block metadataを登録する。
2. prefix_i_tokens * KV bytes/token から移送byte数を計算する。
3. 100Gbps・10kmに基づいて移送遅延を計算する。
4. requestのGPU B到着時刻を移送遅延ぶん遅らせる。
5. GPU Bで通常のprefix matchingを行い、hitしたprefix分をprefillから除外する。
```

つまり、`migrate_kv` は次のコストになる。

```text
E2E TTFT = KV migration latency + queue wait on B + prefill追加分の処理時間
```

一方、`cold` は次のコストになる。

```text
E2E TTFT = queue wait on B + prompt j 全体のprefill処理時間
```

## 追加した主な実装

### `serving/core/router.py`

workload JSONLのfailover fieldsを読むようにした。

追加フィールド:

| Field | Meaning |
| --- | --- |
| `failover_mode` | `cold` または `migrate_kv` |
| `failed_instance_id` | KV cacheを持っていたがdownした想定のinstance |
| `target_instance_id` | failover先のinstance |
| `reuse_prefix_toks` | 移送して再利用したいprefix token数 |
| `kv_migration_bandwidth_gbps` | GPU間帯域。省略時100Gbps |
| `kv_migration_distance_m` | GPU間距離。省略時10000m |

`target_instance_id` が指定されているfailover requestは、通常のrouting policyを使わず、指定instanceへ強制routingする。

`migrate_kv` の場合は、routing時に以下を行う。

- target schedulerの `MemoryModel.seed_migrated_prefix()` を呼ぶ。
- `reuse_prefix_toks` 分のprefixをtarget NPU cacheへ登録する。
- KV移送byte数を計算する。
- 移送遅延を計算する。
- request arrivalを移送遅延ぶん遅らせる。
- `communication_latency_ns` とfailover metadataをrequestへ記録する。

### `serving/core/memory_model.py`

`seed_migrated_prefix(token_ids, prefix_len)` を追加した。

役割:

- target instanceのNPU RadixCacheへ、移送済みprefixを事前登録する。
- prefix長はNPU block sizeに合わせて切り下げる。
- 必要なNPU memoryを確保する。
- メモリ不足時はevictable prefix cacheをevictする。
- 実際に登録できたprefix token数を返す。

### `serving/core/request.py`

requestにfailover/migration計測用フィールドを追加した。

主な追加フィールド:

- `failover_mode`
- `failed_instance_id`
- `failover_target_instance_id`
- `reuse_prefix_toks`
- `kv_migration_tokens`
- `kv_migration_bytes`
- `kv_migration_latency_ns`
- `kv_migration_distance_latency_ns`
- `kv_migration_serialization_latency_ns`
- `kv_migration_bandwidth_gbps`
- `kv_migration_distance_m`

### `serving/core/scheduler.py`

CSV出力にKV migration関連カラムを追加した。

また、`Scheduler.add_request()` がfailover metadataを `Request` へ渡せるようにした。

### `docs/docs/workloads/jsonl-format.md`

workload formatにfailover fieldsを追記した。

## 試験用workload

スモーク用に以下を生成した。

- `workloads/generated/failover_kv/cold.jsonl`
- `workloads/generated/failover_kv/migrate_kv.jsonl`

条件:

| Item | Value |
| --- | ---: |
| prompt `j` length | 1152 tokens |
| reusable prompt `i` length | 1024 tokens |
|追加prefill分 | 128 tokens |
| output length | 16 tokens |
| failed instance | 0 |
| target instance | 1 |
| GPU間帯域 | 100Gbps |
| GPU間距離 | 10km |

`cold`:

```json
{
  "input_toks": 1152,
  "output_toks": 16,
  "arrival_time_ns": 0,
  "failover_mode": "cold",
  "failed_instance_id": 0,
  "target_instance_id": 1,
  "reuse_prefix_toks": 1024
}
```

`migrate_kv`:

```json
{
  "input_toks": 1152,
  "output_toks": 16,
  "arrival_time_ns": 0,
  "failover_mode": "migrate_kv",
  "failed_instance_id": 0,
  "target_instance_id": 1,
  "reuse_prefix_toks": 1024
}
```

実際のファイルにはprefix matching用の `input_tok_ids` / `output_tok_ids` も入れている。

## 実行コマンド

```bash
docker run --rm --platform linux/amd64 \
  -v /Users/taichikondo/LLMServingSim:/app/LLMServingSim \
  -w /app/LLMServingSim \
  astrasim/tutorial-micro2024 \
  bash -lc "pip3 install -q rich pyinstrument pyyaml msgspec 'protobuf>=6,<7' && \
  for mode in cold migrate_kv; do \
    echo RUN_FAILOVER=\$mode; \
    PYTHONPATH=/app/LLMServingSim/astra-sim/extern/graph_frontend/chakra/build/lib \
    python3 -m serving \
      --cluster-config configs/cluster/single_node_multi_instance.json \
      --dataset workloads/generated/failover_kv/\$mode.jsonl \
      --request-routing-policy RR \
      --output results/failover-kv-\$mode.csv \
      --run-id failover-kv-\$mode \
      --log-level WARNING; \
  done"
```

出力:

- `results/failover-kv-cold.csv`
- `results/failover-kv-migrate_kv.csv`

## 結果

| Mode | Target instance | Simulator TTFT | E2E TTFT | Prefill service | Decode after TTFT | Communication / migration | Total latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cold` | 1 | 49.31ms | 49.31ms | 49.31ms | 170.13ms | 0.00ms | 219.44ms |
| `migrate_kv` | 1 | 13.61ms | 24.40ms | 13.40ms | 170.13ms | 10.79ms | 183.74ms |

KV migration details:

| Field | Value |
| --- | ---: |
| migrated tokens | 1024 |
| migrated bytes | 134,217,728 bytes |
| migrated size | 128 MiB |
| distance latency | 0.05ms |
| serialization latency | 10.74ms |
| total migration latency | 10.79ms |

Prefix cache result:

| Mode | NPU prefix hit tokens | Hit ratio |
| --- | ---: | ---: |
| `cold` | 0 | 0.00% |
| `migrate_kv` | 1024 | 88.89% |

## 解釈

この条件では、KV cacheを移送した方が速い。

`cold` はprompt `j` 全体1152 tokensをGPU Bでprefillするため、TTFTは49.31msだった。

`migrate_kv` は1024 tokens分のKV cacheをGPU Bへ移送するため、10.79msの通信コストが発生する。しかし、GPU Bでは残り128 tokensだけをprefillすればよく、prefill serviceは13.40msまで下がった。

結果として:

```text
cold E2E TTFT      = 49.31ms
migrate E2E TTFT   = 10.79ms + 13.61ms = 24.40ms
```

になり、移送込みでも `migrate_kv` が約24.92ms速い。

この実験でのbreak-even条件は概念的には次の不等式で表せる。

```text
KV migration latency < saved prefill latency
```

今回:

```text
KV migration latency = 10.79ms
saved prefill latency ≈ 49.31ms - 13.40ms = 35.92ms
```

なので、移送する価値がある。

## 注意点

今回の実装は「障害イベントでGPU Aを停止し、A上の既存queueを自動でBへ再配置する」完全なfault injectionではない。

現時点で実装したのは、workload上でfailover requestを明示し、

- `cold`: Bで再prefill
- `migrate_kv`: BへKV移送後、prefix hitとして追加分だけprefill

を比較する機能である。

したがって、今回の目的である「GPU Aがdownした際、prompt iのKV cacheをGPU Bに移送する方が、ゼロからprefillするより速いか」を評価するには十分だが、クラスタ全体の障害復旧policyを評価するには追加実装が必要。

## 次にやるとよいこと

prefix長をsweepして、break-even pointを出す。

例:

- prompt `j`: 8192 tokens固定
- `reuse_prefix_toks`: 0, 512, 1024, 2048, 4096, 6144, 7168
- link bandwidth: 25Gbps, 50Gbps, 100Gbps, 200Gbps
- distance: 1km, 10km, 100km

見る指標:

- E2E TTFT
- simulator TTFT
- prefill service
- migration latency
- total latency
- migrated bytes

これにより、どのprefix長・ネットワーク条件ならKV migrationがcold prefillより有利かを定量化できる。
