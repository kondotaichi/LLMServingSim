# 100-GPU都市ワークロード

## 1. 目的

11 km²、人口20万人の高密度都市領域に100台のRTX 4090を配置し、地理routingとTTFTを評価するための再現可能なワークロードである。

## 2. 地理・GPU構成

| 項目 | 設定 |
|---|---:|
| 面積 | 11 km² |
| 領域形状 | 正方形、1辺約3,316.6 m |
| 人口 | 200,000 users |
| 人口密度 | 約18,181.8 users/km² |
| GPU数 | 100 |
| GPU配置 | 10行×10列の等間隔grid |
| 平均担当人口 | 2,000 users/GPU |
| 平均担当面積 | 0.11 km²/GPU |

Userは領域内へ一様ランダムに配置し、最寄りと第2近傍のGPUを事前計算している。静的配置と20,000人のdaily-active user集合は、全workloadとseedで共通である。各requestの送信者はこのdaily-active集合から一様にsamplingする。

## 3. 人口からrequest rateへの変換

人口だけからrequest rateは一意に決まらないため、以下を実験上の明示的な仮定とした。

| 仮定 | 値 |
|---|---:|
| Daily active user比率 | 10% |
| Daily active users | 20,000 |
| 1 active user当たりrequest数 | 20 requests/day |
| 日次request数 | 400,000 requests/day |
| Busy hourへの日次request集中率 | 15% |
| Peak倍率 | Busy hourの2倍 |

この仮定から、次の3水準を生成する。

| Load level | Rate | 3,000 requestsの継続時間 |
|---|---:|---:|
| `daily_average` | 約4.63 rps | 648 s |
| `busy_hour` | 約16.67 rps | 180 s |
| `peak_2x` | 約33.33 rps | 90 s |

各load levelについてseed 1、2、3を用意した。ArrivalはPoisson processから生成し、比較しやすいよう最終時刻が目標継続時間へ一致するようrescaleしている。

## 4. Request長

Input token数は次の離散分布からsamplingする。

| Input tokens | Probability |
|---:|---:|
| 512 | 20% |
| 2,000 | 25% |
| 4,000 | 25% |
| 6,000 | 15% |
| 8,000 | 10% |
| 10,000 | 5% |

Output token数は128、256、512、1,024 tokensを、それぞれ30%、40%、20%、10%でsamplingする。

本workloadは新しい都市負荷のbaselineを確立することを目的とし、prefix reuseは0としている。KV reuse率を導入する実験では、user/session単位で実際に同じprefix contentを持つ拡張workloadを別途生成する。

## 5. 通信モデル

Userから最寄りGPUまでのaccess networkは、次の簡易モデルで表す。

| 項目 | 値 |
|---|---:|
| Access throughput | 100 Mbit/s |
| 距離遅延 | 5 ns/m |
| Protocol overhead | 500 bytes/request |
| Input token payload | 4 bytes/token |
| First-token payload | 100 bytes |

`arrival_time_ns`はuplink完了後のGPU到着時刻であり、元の送信時刻は`request_send_time_ns`へ保存する。無線contention、jitter、packet lossは扱わない。

## 6. RTX 4090 cluster

Cluster設定は[`configs/rtx4090_100gpu.json`](configs/rtx4090_100gpu.json)である。100 nodeに1 GPUずつ配置し、各GPUを独立したlogical instanceとする。Repositoryに含まれるRTX4090・Llama 3.1 8B・TP1のprofileをそのまま使用する。

| 項目 | 値 |
|---|---:|
| GPU memory | 24 GB |
| GPU memory bandwidth | 1,008 GB/s |
| Host memory | 128 GB/node |
| Host memory bandwidth | 33.8 GB/s |
| GPU backbone | 16 GB/s、20 µs |
| Model | Llama 3.1 8B |
| Parallelism | TP=1、PP=1、100 replicas |

GPU・host memory値は既存の[`configs/cluster/ten_node_rtx4090_apn.json`](../../configs/cluster/ten_node_rtx4090_apn.json)と揃えている。

## 7. 出力

- `workloads/*.jsonl`: 3 load levels × 3 seeds
- `placements/users.csv`: 200,000 usersの座標と近傍GPU
- `placements/gpus.csv`: 100 GPUsの座標
- `configs/workload_manifest.csv`: workloadごとのrateとtoken統計
- `configs/experiment.json`: 全仮定と生成結果
- `configs/rtx4090_100gpu.json`: RTX 4090×100台のsimulator cluster config

## 8. 再生成

Repository rootから次を実行する。

```bash
python3 experiments/2026-07-31_new_workload/scripts/prepare_workloads.py
```

生成物は同じseedと設定から決定論的に再現される。

生成後の整合性検証は次を実行する。

```bash
python3 experiments/2026-07-31_new_workload/scripts/validate.py
```

## 9. Simulator実行例

RTX4090 profileはrepository内の`profiler/perf/RTX4090/meta-llama/Llama-3.1-8B/bf16/tp1/`を使用するため、新たなprofilingは不要である。

Simulator containerを起動し、初回のみbuildする。

```bash
./scripts/docker-sim.sh
```

Container内で実行する。

```bash
./scripts/compile.sh
mkdir -p experiments/2026-07-31_new_workload/results/busy_hour_seed1
```

```bash
python3 -m serving \
  --cluster-config experiments/2026-07-31_new_workload/configs/rtx4090_100gpu.json \
  --dataset experiments/2026-07-31_new_workload/workloads/busy_hour_seed1.jsonl \
  --request-routing-policy NEAREST \
  --num-reqs 3000 \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --max-num-seqs 128 \
  --max-num-batched-tokens 2048 \
  --enable-chunked-prefill \
  --no-enable-prefix-caching \
  --graph-converter in-process \
  --trace-io buffered \
  --output experiments/2026-07-31_new_workload/results/busy_hour_seed1/requests.csv \
  --geographic-user-output experiments/2026-07-31_new_workload/results/busy_hour_seed1/users.csv \
  --geographic-gpu-output experiments/2026-07-31_new_workload/results/busy_hour_seed1/gpus.csv \
  --geographic-metadata-output experiments/2026-07-31_new_workload/results/busy_hour_seed1/metadata.json \
  --geographic-users-csv experiments/2026-07-31_new_workload/placements/users.csv \
  --geographic-gpus-csv experiments/2026-07-31_new_workload/placements/gpus.csv \
  --run-id rtx4090-100gpu-busy-hour-seed1 \
  --log-level WARNING
```

最初は`--num-reqs 1`へ変更してsmoke testを行い、その後3,000 requestsへ増やすことを推奨する。
