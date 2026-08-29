# 本郷ワークロード詳細

## 1. この文書でいう「本郷ワークロード」

本郷ワークロードは、東京大学本郷キャンパスを模した地理配置、ShareGPT由来の長文・マルチターンLLMリクエスト、Poisson型の到着過程を組み合わせたシミュレーション用データセットである。

現在は、用途の異なる二つの規模が存在する。

| 呼称 | リクエスト数 | セッション数 | 再訪リクエスト | 主な用途 |
|---|---:|---:|---:|---|
| 正本・2000件版 | 2000 | 1129 | 871（43.55%） | `2026-08-09-test-some-workload`での再検証、再訪・prewarm評価 |
| 初期・300件版 | 300 | 288 | 12（4.00%） | 従来の1x–10x負荷比較、PP=1/PP=2比較 |

Method-fixにおける正本は`workloads/full/hongo_*.jsonl`の2000件版である。300件版は単純に先頭側を切り出した初期実験用データであり、再訪率が大きく低下している。このため、通常のTTFT・PP比較には使用できるが、ユーザー再訪を予測するproactive KV prewarmの評価には適さない。

## 2. 生成元と再現方法

地理・到着・ネットワークの生成ロジックは次のスクリプトを正本とする。

```text
experiments/2026-08-01_hongo_workload/scripts/prepare_hongo_workload.py
```

Method-fixでは`build_hongo_workloads.py`がこの実装を読み込み、出力先だけを`2026-08-09-test-some-workload`配下へ差し替える。これにより元実験のファイルを上書きせず、同じ生成規則を再利用している。

```text
experiments/2026-08-09-test-some-workload/scripts/build_hongo_workloads.py
```

コンテンツはShareGPTのマルチターン会話から、入力長3000–20000 tokensの長い会話を抽出したものを元にしている。`session_id`、`input_tok_ids`、`output_tok_ids`を保持するため、単なるtoken数の人工分布ではなく、同一会話の継続性と実token列を持つ。

## 3. 想定する人口と地理

現在の構成は住宅域を含まず、本郷キャンパス単独である。

| 項目 | 設定 |
|---|---:|
| 対象人口 | 27,000人 |
| キャンパス面積 | 0.56 km² |
| 形状 | 正方形近似 |
| 一辺 | 約748.3 m |
| ユーザー配置 | 正方形内の一様ランダム |
| 配置seed | 20260801 |
| GPU | RTX 4090 × 12 |
| GPU配置 | 3行×4列の等間隔グリッド |

人口27,000人は、本郷キャンパスの学生、教員、職員を合わせた昼間人口の概算である。ユーザー27,000人を正方形内へ一様配置し、各ユーザーを最寄りGPUと次点GPUへ対応付ける。同じセッションは常に同じユーザー、同じ座標へ割り当てる。

正本2000件版における最寄りGPUまでの距離は次のとおりである。

| 指標 | 距離 |
|---|---:|
| 平均 | 84.2 m |
| p50 | 86.4 m |
| p95 | 132.2 m |
| p99 | 144.1 m |
| 最大 | 155.6 m |

## 4. GPUとparallelism

物理GPU数はPPによらず12台に固定する。

### PP=1

- 12 physical GPU
- 12 logical instance
- 1 instanceあたり1 GPU
- `assigned_instance_id = physical_gpu_id`

### PP=2

- 12 physical GPU
- 6 logical instance
- 1 instanceあたり2 GPU
- 物理GPU `(0,1)`, `(2,3)`, …, `(10,11)`をそれぞれ1グループにする
- `assigned_instance_id = physical_gpu_id // 2`

PP=2化ではリクエスト内容、到着時刻、ユーザー位置を変えず、instanceの見せ方だけを6グループへ変換する。`physical_assigned_instance_id`、`pp_group_id`、`pp_stage_id`を残し、物理配置との対応を追跡できる。

PP=1とPP=2を比較する際は、物理GPU台数は同じでもlogical instance数、batching単位、instanceあたりKV容量、scheduler上限の意味が変わる。`max_num_seqs`やtoken budgetを各instanceで同じ値にするとcluster-wide capacityはPP=2が半分になるため、公平比較では全体capacityを明示的に合わせる必要がある。

## 5. リクエスト内容

### 正本2000件版

| 指標 | Input tokens | Output tokens | 再利用可能prefix |
|---|---:|---:|---:|
| 平均 | 4072.9 | 371.8 | 2028.6 |
| p50 | 3659 | 352 | 1824 |
| p95 | 6459.5 | 767 | 3216.8 |
| p99 | 7587.4 | 837.5 | 3792.2 |
| 最小 | 3000 | 1 | 1488 |
| 最大 | 7952 | 2305 | 3968 |

### 初期300件版

| 指標 | Input tokens | Output tokens | 再利用可能prefix |
|---|---:|---:|---:|
| 平均 | 3860.0 | 279.6 | 1922.1 |
| p50 | 3499 | 205.5 | 1744 |
| p95 | 5843.9 | 745.2 | 2915.2 |
| p99 | 6715.0 | 775 | 3346.6 |
| 最小 | 3004 | 1 | 1488 |
| 最大 | 7825 | 1663 | 3904 |

`reuse_prefix_toks`は入力長の50%をKV block size 16 tokensへ切り下げて設定する。

```text
reuse_prefix_toks = floor(input_toks × 0.5 / 16) × 16
```

これは「再利用候補となるprefix長」を表す。実際にprefix cache hitするか、redirect時に移送されるかは、セッション履歴、cache状態、routing方式によって決まる。

## 6. セッションと再訪

正本2000件版は1129セッションを含み、2000件のうち871件が同一セッションの2回目以降である。

| セッション内リクエスト数 | セッション数 |
|---:|---:|
| 1 | 734 |
| 2 | 163 |
| 3 | 112 |
| 4 | 49 |
| 5 | 41 |
| 6 | 14 |
| 7 | 11 |
| 8 | 3 |
| 9 | 2 |

一方、初期300件版は288セッションで、内訳は1リクエスト279セッション、2リクエスト6セッション、3リクエスト3セッションである。再訪は12件しかない。

この差は重要である。

- TTFT、queueing、PP比較: 300件版でも評価可能
- KV migration: redirectが発生すれば300件版でも評価可能
- proactive prewarm: 300件版は再訪信号が少なすぎる
- 再訪予測: 2000件版、または再訪率を事前定義した評価用データを使う

## 7. 到着過程と負荷倍率

キャンパスの利用仮定は次のとおりである。

| 項目 | 設定 |
|---|---:|
| DAU比率 | 20% |
| 1 active userあたり | 40 requests/day |
| 1日総リクエスト | 216,000 |
| daily average | 2.5 requests/s |
| busy hourへの集中 | 1日量の15% |
| busy hour | 9 requests/s |

各リクエストの到着間隔は指数分布から生成する。ただし、サンプルした全間隔を最後にスケールし、全体継続時間が`N / target_rate`へ一致するよう補正する。したがって、局所的にはPoisson的な揺らぎを持つが、全体の平均レートは設定値へ固定される。

1xはbusy hourの9 rpsである。2xは18 rpsとして独立に生成し、3x以上は2xのリクエスト内容と順序を保持したまま時間軸だけを`2 / multiplier`倍へ圧縮する。

| 負荷 | 目標レート | 300件版の到着時刻幅 | 2000件版の想定継続時間 |
|---:|---:|---:|---:|
| 1x | 9 rps | 33.27 s | 222.22 s |
| 2x | 18 rps | 16.55 s | 111.11 s |
| 3x | 27 rps | 11.03 s | 74.07 s |
| 4x | 36 rps | 8.27 s | 55.56 s |
| 5x | 45 rps | 6.62 s | 44.44 s |
| 6x | 54 rps | 5.52 s | 37.04 s |
| 7x | 63 rps | 4.73 s | 31.75 s |
| 8x | 72 rps | 4.14 s | 27.78 s |
| 9x | 81 rps | 3.68 s | 24.69 s |
| 10x | 90 rps | 3.31 s | 22.22 s |

300件版の「到着時刻幅」は最初と最後の`request_send_time_ns`の差である。生成器は時刻0から最初の指数間隔後に1件目を置くため、`N / rate`そのものよりわずかに短く見える。料金分析等で使うシミュレーション完了時間は、この到着時刻幅ではなく、最後のリクエスト処理完了までを含む。

## 8. ネットワークモデル

各リクエストのネットワーク入力は、ユーザー座標、GPU座標、input token数から事前計算する。

| 項目 | 設定 |
|---|---:|
| throughput | 100 Mbps |
| 距離遅延 | 5 ns/m |
| protocol overhead | 500 bytes |
| input token | 4 bytes/token |
| first-token payload | 100 bytes |

```text
request_payload_bytes = 500 + input_toks × 4
serialization_ns = 8000 × payload_bytes / 100
uplink_latency = distance × 5 ns/m + request serialization
downlink_latency = distance × 5 ns/m + first-token serialization
communication_latency = uplink_latency + downlink_latency
```

正本2000件版のcommunication latencyは平均1.352 ms、p50 1.220 ms、p95 2.116 ms、p99 2.477 msである。これはユーザーから同一キャンパス内の最寄りGPUへ接続する元の本郷配置での値である。

現在のシミュレーションではnetwork contentionは無効であり、複数転送が同時に発生しても100 Mbpsリンクの帯域を互いに奪い合わない。このため、とくにKV migrationへ有利な可能性がある。

## 9. 300件の東京・鹿児島比較版

`2026-08-02-kagosima-tokyo`で行ったPP=1/PP=2および電力料金比較では、本郷の300件版について、コンテンツと到着時間を保持したまま別の地理配置へ写像した。

### 全東京配置

- 12 physical GPUすべて東京
- PP=1: 12 instanceへ各25リクエスト
- PP=2: 6 logical instanceへ各50リクエスト
- 平均communication latency: 約1.285 ms

### 東京＋鹿児島配置

- 東京6 GPU、鹿児島6 GPU
- 300件のうち東京150件、鹿児島150件
- PP=1: 12 instanceへ各25リクエスト
- PP=2: 6 logical instanceへ各50リクエスト
- 平均communication latency: 約6.534 ms

この変換では、同一セッションを同一ユーザーへ固定したまま、session単位でinstance負荷が均等になるよう再配置している。その結果、元の本郷配置にあったGPUごとの自然な件数差はなくなり、PP=1では完全に25件ずつ、PP=2では50件ずつになる。

したがって、この東京・鹿児島版は「本郷コンテンツと時間分布を使った均等配置の地理比較」であり、元の本郷キャンパス内で自然に最寄りGPUへ割り当てた空間分布そのものではない。

## 10. Method-fix内の派生ワークロード

Method-fixでは、評価したい機構ごとに正本から派生データを作っている。これらを通常の本郷ワークロードと混同してはならない。

| 種類 | 目的 | 主な変更 |
|---|---|---|
| `workloads/full/` | 2000件の正本 | 通常の地理・時間分布 |
| `workloads/pp1/load_*x.jsonl` | PP=1負荷プローブ | 到着時間を圧縮 |
| `workloads/pp2/load_*x.jsonl` | PP=2負荷プローブ | PP grouping＋到着時間圧縮 |
| `load_12x_hot50.jsonl` | redirect/KV migration評価 | 50%を特定instanceへ集中 |
| `revisit45_300/` | prewarm評価 | 300件で再訪率45%となるよう構成 |
| `extreme_router_burst_*` | router queue機構確認 | 短時間burst |
| `pp2_router_queue_two_wave_*` | 全候補capacity超過確認 | 二波到着、極小`max_num_seqs` |

通常負荷は比較的均等であり、PP=2では10xを超えてもredirectが発生しにくい。hotspot版は特定instanceだけを先に容量不足にし、他instanceへ移送可能な余地を意図的に残す。これはKV migrationの機構を確認するストレステストであり、現実の本郷トラフィックをそのまま再現したものではない。

## 11. 解釈上の制約

本郷ワークロードを使った結果には、次の制約がある。

- 基本実験はseed 1のみであり、到着・ユーザー配置のばらつきを評価していない
- ユーザーはキャンパス内で一様分布であり、建物、講義室、時間帯hotspotを持たない
- ShareGPTコンテンツは学生・研究者専用の会話分布ではない
- 300件版は再訪率が4%しかなく、prewarm評価に不適切
- 東京・鹿児島版はinstanceごとの件数を人工的に均等化している
- hotspot、burst、revisit45は機構検証用の人工派生条件である
- network contention、CPU、実ネットワークの輻輳はモデル化されていない
- PP=1とPP=2ではpipeline以外にlogical instance数とbatching挙動も変わる

結果を報告する際は、少なくともリクエスト数、再訪率、負荷倍率、空間分布、PP、cluster-wide capacity、派生ワークロードの有無を併記する必要がある。
