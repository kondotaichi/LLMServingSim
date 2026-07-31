# 20-GPU都市ワークロード

## 1. 目的

11 km²、人口20万人の高密度都市領域に20台のGH200級GPUを配置し、地理routingとTTFTを評価するための再現可能なワークロードである。

## 2. 地理・GPU構成

| 項目 | 設定 |
|---|---:|
| 面積 | 11 km² |
| 領域形状 | 正方形、1辺約3,316.6 m |
| 人口 | 200,000 users |
| 人口密度 | 約18,181.8 users/km² |
| GPU数 | 20 |
| GPU配置 | 4行×5列の等間隔grid |
| 平均担当人口 | 10,000 users/GPU |
| 平均担当面積 | 0.55 km²/GPU |

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

## 6. GH200級cluster

GH200実機用のcluster設定は[`configs/gh200_20gpu.json`](configs/gh200_20gpu.json)である。20 nodeに1 GPUずつ配置し、各GPUを独立したlogical instanceとする。GH200 profileがない環境で構成だけを確認する場合は[`configs/gh200_class_20gpu_proxy.json`](configs/gh200_class_20gpu_proxy.json)を使用する。

| 項目 | 値 |
|---|---:|
| GPU memory | 96 GB HBM3 |
| GPU memory bandwidth | 4,000 GB/s |
| Grace CPU memory | 480 GB LPDDR5X |
| Grace CPU memory bandwidth | 500 GB/s |
| GPU backbone | 50 GB/s、20 µs |
| Model | Llama 3.1 8B |
| Parallelism | TP=1、PP=1、20 replicas |

NVIDIAのGH200仕様には96 GB HBM3・最大4 TB/sと144 GB HBM3e・最大4.9 TB/sの構成がある。本実験では保守的な96 GB HBM3構成を採用した。

重要な制約として、現在のrepositoryにはGH200の`profiler/perf`データがない。そこでcluster configの`hardware`は実行可能性のため`RTXPRO6000`を指定し、計算latencyは既存profileをproxyとして使う。Memory capacityとbandwidthはGH200級だが、GH200の計算性能を正確に再現する設定ではない。正確な評価にはGH200実機でprofileを取得し、`hardware`を置き換える必要がある。

GH200仕様の根拠は[NVIDIA Grace Performance Tuning Guide](https://docs.nvidia.com/dccpu/grace-perf-tuning-guide/index.html)を参照する。

## 7. 出力

- `workloads/*.jsonl`: 3 load levels × 3 seeds
- `placements/users.csv`: 200,000 usersの座標と近傍GPU
- `placements/gpus.csv`: 20 GPUsの座標
- `configs/workload_manifest.csv`: workloadごとのrateとtoken統計
- `configs/experiment.json`: 全仮定と生成結果
- `configs/gh200_20gpu.json`: GH200実機profileを使用するcluster config
- `configs/gh200_class_20gpu_proxy.json`: simulator cluster config

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
