# 10セルAPN・KV migrationシミュレーション設定仕様

## 1. 目的

RTX 4090を1基ずつ搭載した10ノードを100 km²の領域へ分散配置し、20ユーザからの
ShareGPTベース負荷を処理する。混雑時の待機、UE再送、GPU forward、GPU間KV migration
を比較し、KV migration関連機能が意図どおり動作するか確認する。

主な成果物は、4方式のリクエスト単位CSV、TTFT breakdown図、TTFT CDF、GPU別負荷・
リダイレクト・KV migration統計とする。初回実験に数値上の合格基準は設けない。

## 2. 比較する4方式

| 方式 | ポリシー | 最寄りGPUが満杯の場合 |
| --- | --- | --- |
| リダイレクトなし | `NEAREST_KV` | 最寄りGPUで待機し、ローカルKVを利用 |
| UE resend | `NEAREST_REJECT` | UEへ一度返し、第2近傍GPUへ再送。転送先ではcold prefill |
| GPU forward | `NEAREST_MIGRATE` | GPU間でリクエストを転送。転送先ではcold prefill |
| GPU forward + KV | `NEAREST_MIGRATE_KV` | GPU間でリクエストと再利用可能KVを転送 |

全方式で、リクエスト内容、到着時刻、ユーザ・GPU座標、初期KV状態、乱数seedを共通に
する。

## 3. モデルと実行設定

| 項目 | 設定 |
| --- | --- |
| モデル | `meta-llama/Llama-3.1-8B` |
| Weight dtype | `bfloat16`（profile variant名は`bf16`） |
| KV cache dtype | `auto`（Weightと同じBF16相当） |
| TP / PP | TP=1、PP=1 |
| GPU数 | 10 |
| 物理ノード数 | 10 |
| 1ノードあたりGPU | RTX 4090 × 1 |
| GPUメモリ容量 | 24 GB/基 |
| GPUメモリ帯域 | 1008 GB/s |
| `max_num_seqs` | 128（本設定） |
| `max_num_batched_tokens` | 2048 |
| `block_size` | 16 |
| Chunked prefill | 有効 |
| Prefix caching | 有効 |
| Prefill優先 | 無効 |
| Weight offload | 無効。WeightはGPUに固定 |

RTX4090/Llama-3.1-8Bの既存profileはTP=1、`max_num_batched_tokens=2048`、
`max_num_seqs=256`、最大KV長16384で作成されている。本設定はこの主要範囲内とする。

## 4. 10セルとGPU配置

### 4.1 領域

- 幅: 10,000 m
- 高さ: 10,000 m
- 面積: 100,000,000 m² = 100 km²
- 座標原点: 領域左下を`(0, 0)`とする
- 「州」という地理的意味は持たせず、10個の匿名セルとして扱う

### 4.2 GPU座標

10基を同一直線上に置かず、平面上で間隔が均等に近くなる3–4–3の千鳥格子へ配置する。
隣接GPU間隔は概ね3.33 kmである。

| GPU/instance ID | x (m) | y (m) | 負荷系列ラベル |
| ---: | ---: | ---: | --- |
| 0 | 1666.667 | 2113.249 | Arizona |
| 1 | 5000.000 | 2113.249 | California |
| 2 | 8333.333 | 2113.249 | Florida |
| 3 | 0.000 | 5000.000 | Illinois |
| 4 | 3333.333 | 5000.000 | Michigan |
| 5 | 6666.667 | 5000.000 | New York |
| 6 | 10000.000 | 5000.000 | Ohio |
| 7 | 1666.667 | 7886.751 | Pennsylvania |
| 8 | 5000.000 | 7886.751 | Texas |
| 9 | 8333.333 | 7886.751 | Virginia |

負荷系列ラベルはWildChat CSVの10系列との対応を再現するための識別子にすぎず、実際の
州の位置・距離・タイムゾーンは表現しない。

### 4.3 ユーザ座標

- ユーザ数: 20
- 乱数seed: 42
- 各ユーザの座標は10 km × 10 km領域内からランダム生成する
- セル別負荷を確実に表現するため、各GPUのVoronoiセル内に2ユーザずつ入るよう
  層化ランダム生成する
- 各セル内では一様ランダムとし、GPU座標と同一点になることを要求しない
- 座標はワークロード生成時に一度だけ決定し、4方式で固定する
- 各ユーザの最寄りGPUを通常の処理先、第2近傍GPUをredirect/migration先とする

全領域から完全に独立な一様乱数で20人を生成すると、ユーザが0人のセルが生じて
WildChat由来の10セル負荷比率を再現できない可能性がある。このため、初回実験では
「領域内ランダム」という条件を保ちつつ、セルごとに2人を保証する層化方式を採用する。

## 5. ワークロード

### 5.1 入力データ

- リクエスト内容・トークンID:
  `workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl`
- 地域・時間帯負荷比率:
  `/Users/taichikondo/wildchat-analysis/results/figures/state/10min/us/01_top_regions_time_requests.csv`
- 使用列: `avg_requests_per_active_user`
- 使用区分: `weekday`
- 乱数seed: 42

### 5.2 負荷生成

- ShareGPTの重複のない300リクエストを1回ずつ使用する
- WildChatの値は絶対リクエスト数ではなく、10セル間および時間帯間の相対比率に使う
- WildChatのローカル24時間分の比率を60秒へ線形圧縮する
- 総リクエスト数: 300
- 到着期間: 約0～60秒
- 平均到着率: 5 requests/s
- ユーザは所属セル内の2人へ交互に割り当てる
- セッション固定は行わない。ただし初期KVは最寄りGPUに存在すると仮定する

現在の基礎ワークロード:

`workloads/generated/wildchat_regional/sharegpt_300_weekday_ratio_1min.jsonl`

この基礎ワークロードへ固定GPU/ユーザ座標、通信情報、第2近傍GPU、KV再利用情報を付加
した最終geographic workloadを別ファイルとして生成する。

## 6. APN通信設定

| 項目 | 設定 |
| --- | --- |
| GPU間帯域 | 10.7 Gbit/s |
| バイト換算 | 1.3375 GB/s |
| GPU間RTT | 0.601 ms = 601,000 ns |
| GPU間片道伝搬時間 | 0.3005 ms = 300,500 ns |
| ノード間差 | なし。全ノード間で統一 |
| 同時転送時の帯域 | 初回は各転送が10.7 Gbit/sを利用可能と仮定 |
| APN全体の帯域競合 | 初回はモデル化しない |

一方向のrequest/KV転送時間は、固定片道遅延とserialization時間の和として扱う。

```text
apn_transfer_ns = 300500 + bytes * 8 / 10.7e9 * 1e9
```

UE resendの容量確認往復にはRTT 601,000 nsを使用する。現行実装がGPU間遅延を物理距離
×固定係数で計算している箇所は、今回の固定APN RTTを直接使用できるよう設定または実装を
調整し、架空の距離へ換算して代用しない。

## 7. KV cacheの前提

### 7.1 初期状態

- 各リクエストの再利用可能KVは、そのユーザの最寄りGPUに存在する
- 4方式すべてで同じ初期KV状態を使用する
- 初回はメタデータseed方式を採用し、別のprimingリクエストは送らない
- Prefix cacheは有効にする

### 7.2 KV再利用率

- `kv_reuse_ratio`をワークロード生成パラメータとして実装する
- 初回値: 0.5（入力トークンの50%）
- 各リクエストでは次のように計算する

```text
reuse_prefix_toks = floor(input_toks * kv_reuse_ratio)
```

- `block_size=16`に合わせ、実際にseedするトークン数は16トークン単位で切り下げる
- 後続の感度分析では0.2、0.5、0.8を比較可能にする

## 8. CPUメモリとKV退避・migration経路

### 8.1 ノードごとのCPUメモリ

| 項目 | 設定 |
| --- | --- |
| 容量 | 128 GB/ノード |
| 帯域 | 33.8 GB/s |
| latency | 102.9 ns |

### 8.2 配置方針

- WeightはGPUに固定し、CPUへoffloadしない
- 通常のKV cacheはGPUに保持する
- GPU KV容量超過時だけ、同一ノードのCPUメモリへevictする
- CPUからGPUへ戻す際の帯域33.8 GB/sとlatency 102.9 nsを計上する
- GPU間KV migrationはCPU stagingを経由する

### 8.3 KV migration時間モデル

source GPU→source CPU、APN転送、target CPU→target GPUを直列に計上する。初回は
CPUメモリ帯域をGPU–CPU stagingの実効帯域としても使用する。

```text
source_staging_ns = 102.9 + kv_bytes / 33.8e9 * 1e9
apn_ns            = 300500 + kv_bytes * 8 / 10.7e9 * 1e9
target_staging_ns = 102.9 + kv_bytes / 33.8e9 * 1e9

kv_migration_ns = source_staging_ns + apn_ns + target_staging_ns
```

PCIe帯域を別に与えていないため、上式はCPUメモリ帯域を含む実効stagingモデルである。
実機のPCIe計測値が得られた場合は、source/target staging項をPCIe実測値へ置き換える。

## 9. ルーティング容量と実験ケース

### 9.1 redirect容量判定

最寄りGPUは、次の両条件を満たす場合だけ新規リクエストを受け入れる。

1. `running_reqs < max_num_seqs`
2. 再利用prefixと次回prefill chunkをblock単位で配置するためのKV容量が、現在の
   NPU物理空き容量以内

次回chunkは`max_num_batched_tokens`と、0より大きい場合の
`long_prefill_token_threshold`で制限する。evict可能なcache領域は空き容量に含めず、
新たなevictionが必要な場合はredirect対象とする。これにより、
`max_num_seqs=128`未満でもKV cache容量が不足すればredirectが発生する。

### 9.2 機能確認ケース

- `max_num_seqs=1`
- 目的: redirect、GPU forward、KV migrationが確実に1件以上発生することを確認
- 300件・60秒ワークロードを使用

### 9.3 本設定ケース

- `max_num_seqs=128`
- 目的: 指定された本来のスケジューラ容量で4方式を比較
- redirectが0件の場合も結果として記録し、方式差を作るために条件を後から変更しない

### 9.4 Prefix cache無効ケース

KV方式を含む4方式比較とは分離する。

- cold baseline: `NEAREST`、`NEAREST_REJECT`、`NEAREST_MIGRATE`
- KV効果: `NEAREST`対`NEAREST_KV`、および`NEAREST_MIGRATE`対
  `NEAREST_MIGRATE_KV`

## 10. 出力と可視化

### 10.1 必須出力

- 方式ごとのリクエスト単位CSV
- ユーザ単位集計CSV
- GPU単位集計CSV
- 実験設定・seed・入力ファイルhashを含むmetadata JSON

### 10.2 指標

- TTFT: p50、p95、max
- TPOT: p50、p95、max
- E2E latency: p50、p95、max
- Request throughput
- Output token throughput
- GPU別処理リクエスト数
- GPU別最大キュー長
- redirect件数・率
- source/target GPU別redirect件数
- KV migration件数・総bytes
- KV migration時間: p50、p95、max
- ローカルKV hit tokens・率
- 移送KV hit tokens・率
- cold prefill tokens

### 10.3 図

- 4方式のTTFT breakdown
- 4方式のTTFT CDF
- GPU別リクエスト数
- GPU別redirect件数
- KV migration時間分布

breakdownとCDFは、既存のexp212成果物と同形式を目標にする。

## 11. 実装・実行前チェックリスト

- [ ] 3–4–3 GPU固定座標をcluster/workload生成へ反映
- [ ] seed 42で各Voronoiセル内に2ユーザを生成し、座標を固定
- [ ] 第1・第2近傍GPUを座標から計算
- [ ] APN固定RTTと帯域をrequest forwardへ反映
- [ ] `kv_reuse_ratio`をパラメータ化し、初回0.5で付与
- [ ] 最寄りGPUへ初期KVをmetadata seed
- [ ] CPU stagingを含むKV migration時間を実装・確認
- [ ] 10ノード×RTX4090クラスタJSONを作成
- [ ] `max_num_batched_tokens=2048`を設定
- [ ] `max_num_seqs=1`の機能確認を先に実行
- [ ] redirect件数、KV migration件数、migration bytesが0でないことを確認
- [ ] `max_num_seqs=128`の本設定を実行
- [ ] 4方式のCSV、breakdown、CDFを生成

## 12. 初回実験で採用する仮定

明示指定されていない項目には、以下の推奨値を採用する。

- GPU/ユーザ配置seed: 42
- ユーザ数: 各セル2人、合計20人
- APN bandwidth contention: なし
- Weight placement: GPUのみ
- KV placement: 通常GPU、容量超過時CPU
- Prefix cache: 有効
- Chunked prefill: 有効
- `block_size`: 16
- Request routing以外の乱数seedも可能な限り42へ統一
- 生成物は方式ごとに別パスへ保存し、共通入力を上書きしない
