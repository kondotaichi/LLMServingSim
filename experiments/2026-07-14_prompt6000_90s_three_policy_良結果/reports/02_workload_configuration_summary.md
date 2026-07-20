# Prompt 6000・60秒/90秒ワークロード設定まとめ

作成日: 2026-07-14

## 1. 目的

10セルに分散したGPU環境で、地域的な負荷偏りとKV cache容量不足がある場合の
次の3方式を比較するためのワークロードである。

- A: `NEAREST_KV` — 最寄りGPUで待ち、ローカルKVを再利用
- B: `NEAREST_MIGRATE` — 容量不足時に第2近傍GPUへredirectし、cold prefill
- C: `NEAREST_MIGRATE_KV` — 容量不足時に第2近傍GPUへredirectし、KVもhandoff

60秒版と90秒版は同じ300リクエストを使用する。90秒版はリクエスト内容や地域配置を
変更せず、60秒版の到着間隔だけを1.5倍へ拡大した負荷感度実験である。

## 2. 60秒版と90秒版

| 項目 | 60秒版 | 90秒版 |
|---|---:|---:|
| リクエスト数 | 300 | 300 |
| 到着span | 59.7366 s | 89.6050 s |
| 概算投入率 | 5.00 req/s | 3.33 req/s |
| 入力長 | 全件6,000 tokens | 同一 |
| 出力長 | ShareGPT由来 | 同一 |
| Prefix再利用量 | 全件2,992 tokens | 同一 |
| Request ID・token ID | 基準 | 同一 |
| ユーザ・GPU座標 | 基準 | 同一 |
| 所属セル・第2近傍GPU | 基準 | 同一 |

使用データセット:

```text
60秒: workloads/generated/cell_apn/prompt6000/sharegpt_300_prompt6000_reuse50.jsonl
90秒: workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl
```

90秒版は先頭到着時刻を基準に、次の式で生成した。

```text
new_arrival = base_arrival + round((old_arrival - base_arrival) * 1.5)
```

`request_send_time_ns`も同じ量だけ移動するため、既存の通信区間は維持される。

## 3. リクエスト長

入力はKV容量とprefill負荷を強くするため、全件6,000 tokensへ固定した。
出力長はShareGPT由来の分布を維持している。

| Output tokens | 値 |
|---|---:|
| 最小 | 512 |
| 平均 | 652.5 |
| p50 | 632 |
| p95 | 773 |
| 最大 | 1,021 |

したがって、1リクエストの最大contextは概ね6,512〜7,021 tokensとなる。

## 4. Prefix KV再利用

Prefix再利用率は50%を指定し、block size 16で切り下げた。

```text
floor(floor(6000 * 0.5) / 16) * 16 = 2,992 tokens
2,992 / 6,000 = 49.87%
```

- Aは最寄りGPU上の2,992-token KVを利用する。
- Bは最寄りGPUに残る場合だけ利用し、redirect時はcold prefillとなる。
- Cはredirect時に2,992-token KVを移送し、残り約3,008 tokensを計算する。

## 5. 地理配置と地域負荷

- 領域: 10 km × 10 km
- GPU: 10台を3-4-3の千鳥格子へ固定配置
- ユーザ: 20人、各GPUのVoronoiセルに2人ずつ配置
- ユーザ配置: セル内一様ランダム、seed 42
- 各ユーザの最寄りGPUと第2近傍GPUは固定
- User mobilityなし

ユーザ数はセルごとに同じだが、元の地域別request比率を維持するため、所属セル別の
リクエスト数には偏りがある。

| Home GPU | Requests |
|---:|---:|
| 0 | 24 |
| 1 | 30 |
| 2 | 22 |
| 3 | 35 |
| 4 | 49 |
| 5 | 25 |
| 6 | 30 |
| 7 | 29 |
| 8 | 30 |
| 9 | 26 |

GPU 4が49件で主要hotspotであり、GPU 2の22件に対して2倍以上の負荷を持つ。
この地域skewは60秒版と90秒版で同一である。

## 6. Clusterとscheduler

| 項目 | 設定 |
|---|---|
| Node/GPU | 10 node、各node 1 GPU |
| GPU | RTX 4090、24 GB |
| Model | `meta-llama/Llama-3.1-8B` |
| TP | 1 |
| Weight dtype | bfloat16 |
| KV dtype | auto（bf16相当） |
| `max_num_seqs` | 128 |
| `max_num_batched_tokens` | 2,048 |
| Block size | 16 |
| Chunked prefill | 有効 |
| Prefix caching | 有効 |

入力6,000 tokensはtoken budget 2,048を超えるため、prefillは複数iterationへ分割される。
Routerの容量判定はsequence数だけでなく、各リクエストのblock丸め後の最大context KVを
予約できるか確認する。このため、実行中request数が`max_num_seqs`未満でも
`npu_memory`を理由にredirectが発生する。

## 7. 通信モデル

| 項目 | 設定 |
|---|---:|
| APN帯域 | 10.7 Gbit/s |
| APN固定片道伝搬遅延 | 300,500 ns |
| CPU staging帯域 | 33.8 GB/s |
| CPU staging固定遅延 | 102.9 ns |
| Request payload | 24,500 bytes |
| First-token payload | 100 bytes |

Request payloadは500-byte protocol overheadと4 bytes/tokenから算出した。

```text
500 + 6,000 * 4 = 24,500 bytes
```

CのKV handoffはsource GPU→CPU staging、APN転送、CPU→target GPU stagingを直列に
計上する。2,992-token KVは約392 MBで、今回の1件あたり転送時間は約316.7 msである。

次の要素は無効である。

- Network contention
- Network queueing
- Jitter
- Packet loss
- User mobility

そのため、同時KV転送による帯域競合はモデル化していない。

## 8. 2つの負荷領域の意味

### 60秒版

平均投入率が約5 req/sの高負荷条件である。Hotspotだけでなく移送先GPUも混雑し、
B/Cでも長いRouter queueが残る。クラスタ全体が混雑した場合のredirectとKV handoffを
評価する条件である。

### 90秒版

到着間隔を1.5倍にして約3.33 req/sへ下げた条件である。地域skewは同じなのでGPU 4の
hotspotは残るが、周辺GPUには余力が生まれる。クラスタ全体には余力があり、一部セルだけが
混雑する場合のredirect効果を評価する条件である。

この違いにより、60秒版ではB/Cの移送先にもqueueが残る一方、90秒版では少数のredirectで
hotspotを吸収し、tail latencyを大きく削減できる。

## 9. 関連ファイル

- [60秒版実験README](../../2026-07-13_prompt6000_three_policy/README.md)
- [90秒版実験README](../README.md)
- [90秒版の詳細分析](01_three_policy_analysis.md)
- [到着時刻スケーリング手順](../../../kondoFolder/tutorial/detail/scale_workload_arrivals.md)

