# 2026-07-03: geographicワークロードでのTTFT分解 — 地理的RTTは効くか

日付: 2026-07-03
ブランチ: `experiment/sim-hack`

## 背景・目的

前段の作業として、`configs/cluster/single_node_multi_instance.json`（2GPU独立インスタンス、
`workloads/example_trace.jsonl` 10リクエスト）でTTFTをbreakdownしたところ、
実行時間（`TTFT - queuing_delay`）がプロファイルCSVの単純な層別合計より
約2.4倍大きいという未解明のギャップが見つかった（詳細は本セッションの前半、
別記録なし・会話内のみ）。その際「地理的な距離に起因するRTTが原因では」という
仮説が出たが、`example_trace.jsonl` はgeographic形式のワークロードではなく
（`user_id`/`distance_m`等のフィールドを持たない）、`Request.communication_latency_ns`
は常に0であることを確認し、この仮説は棄却した。

そこで改めて、`python -m workloads.generators geographic` で実際に地理分散ワークロードを
生成し、2GPU・約100リクエスト規模で「地理的距離由来のRTTがTTFTのボトルネックに
なり得るか」を直接検証した。さらにモバイル回線を想定して帯域を下げた場合の感度も見た。

## 実験セットアップ

### ソースワークロード
`workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl`（ShareGPTベース、Llama-3.1-8B用、
300リクエスト）の先頭100行を切り出したもの。

```bash
head -100 workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl > workloads/generated/geo_2gpu100_src.jsonl
```

- リクエスト数: 100
- 送信時刻(arrival_time_ns): 0.047s 〜 9.147s（約9秒間に100件、平均約11 req/s）
- 入力長: min 268 / max 3452 / 平均883 / 中央値777 トークン
- 出力長: min 516 / max 822 / 平均685 / 中央値720 トークン

### クラスタ構成
`configs/cluster/single_node_multi_instance.json`（1ノード・2インスタンス、
各 `meta-llama/Llama-3.1-8B` on `RTXPRO6000`, TP=1, bf16）。

### geographicワークロード生成
`python -m workloads.generators geographic` を2条件で実行（帯域のみ変更、他は同一seed=42）。

| パラメータ | 条件A（通常回線） | 条件B（モバイル回線想定） |
| --- | ---: | ---: |
| `--network-throughput-mbps` | 100 | **10** |
| `--distance-latency-ns-per-meter` | 5.0（既定） | 5.0（既定） |
| `--protocol-overhead-bytes` | 500（既定） | 500（既定） |
| `--bytes-per-input-token` | 4（既定） | 4（既定） |
| `--first-token-payload-bytes` | 100（既定） | 100（既定） |
| `--area-width-m` / `--area-height-m` | 1000 / 1000（既定） | 1000 / 1000（既定） |
| `--num-users` | 100 | 100 |
| `--gpu-rows` / `--gpu-cols` | 1 / 2（＝2GPU） | 1 / 2 |
| `--allow-uniform-user-fallback` | あり（頻度ファイル未指定のため） | あり |
| `--seed` | 42 | 42 |

生成結果の実測統計（共通）:
- ユーザ-GPU距離: min 31.8m / max 519.2m / 平均276.1m
- GPU割当: instance0に49件、instance1に51件
- 100件は60ユーザ由来（`--allow-uniform-user-fallback` により毎回ランダム選択、
  1件のみのユーザ34人、2件17人、…最大5件のユーザも1人）
- リクエストペイロード: `500 + input_toks*4` bytes、min 1572 / max 14308 / 平均4031 bytes

### 実行コマンド

```bash
# 1. geographicワークロード生成（帯域以外は条件A/Bで共通）
python3 -m workloads.generators geographic \
  --input workloads/generated/geo_2gpu100_src.jsonl \
  --output workloads/generated/geo_2gpu100_mobile10_workload.jsonl \
  --users-output workloads/generated/geo_2gpu100_mobile10_users.csv \
  --gpus-output workloads/generated/geo_2gpu100_mobile10_gpus.csv \
  --metadata-output workloads/generated/geo_2gpu100_mobile10_gen_metadata.json \
  --num-users 100 --gpu-rows 1 --gpu-cols 2 \
  --network-throughput-mbps 10 \
  --allow-uniform-user-fallback --seed 42

# 2. シミュレーション実行（servingsim_docker コンテナ内、NEARESTルーティング）
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_mobile10_workload.jsonl \
  --request-routing-policy NEAREST \
  --num-req 100 \
  --output outputs/geo_2gpu100_mobile10_run.csv \
  --run-id geo_2gpu100_mobile10 \
  --log-level WARNING \
  --geographic-user-output outputs/geo_2gpu100_mobile10_users_agg.csv \
  --geographic-gpu-output outputs/geo_2gpu100_mobile10_gpus_agg.csv \
  --geographic-metadata-output outputs/geo_2gpu100_mobile10_run_metadata.json \
  --geographic-users-csv workloads/generated/geo_2gpu100_mobile10_users.csv \
  --geographic-gpus-csv workloads/generated/geo_2gpu100_mobile10_gpus.csv"
```

条件Aは同名から `mobile10` を除いたファイル名（`geo_2gpu100_workload.jsonl` 等）で
同様に実行した。`chakra` は `servingsim_docker`（`astrasim/tutorial-micro2024`
イメージ、既存の永続コンテナ）に導入済みで追加作業は不要だった。

出力ファイル:
- リクエスト単位: `outputs/geo_2gpu100_run.csv` / `outputs/geo_2gpu100_mobile10_run.csv`
- ユーザ単位集計: `outputs/geo_2gpu100_users_agg.csv` / `outputs/geo_2gpu100_mobile10_users_agg.csv`
- GPU単位集計: `outputs/geo_2gpu100_gpus_agg.csv` / `outputs/geo_2gpu100_mobile10_gpus_agg.csv`
- ランメタデータ: `outputs/geo_2gpu100_run_metadata.json` / `outputs/geo_2gpu100_mobile10_run_metadata.json`

## 結果

### TTFT内訳(全100リクエストで集計、`outputs/geo_2gpu100*_run.csv`より)

`2026-07-03-ttft-breakdown-routing-report.md` のPolicy別テーブルと同じ形式で、
Policyの代わりに通信帯域条件を行にし、RTT列を追加した。RTT列は
`communication_latency_ns`(uplink+downlink、伝搬遅延+シリアライズ時間の合計)。

| 条件 | 平均TTFT | P99 TTFT | 平均Queue | P99 Queue | 平均Prefill | P99 Prefill | 平均RTT | P99 RTT | Queue比率 | Prefill比率 | RTT比率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `100Mbps` | 68.78ms | 227.43ms | 14.05ms | 94.61ms | 54.40ms | 183.77ms | 0.333ms | 0.975ms | 20.4% | 79.1% | 0.48% |
| `10Mbps(モバイル)` | 71.60ms | 215.47ms | 13.10ms | 68.66ms | 55.19ms | 178.19ms | 3.308ms | 9.729ms | 18.3% | 77.1% | 4.62% |

観察:

- 帯域を1/10にするとRTT(通信)の絶対値は約10倍(平均0.333ms→3.308ms、P99
  0.975ms→9.729ms)になり、TTFTに占める割合も0.48%→4.62%に増えた。
- それでもQueue比率(18〜20%)・Prefill比率(77〜79%)が支配的な構図は変わらず、
  RTT比率は両条件とも5%未満。
- ユーザ単位集計(`outputs/geo_2gpu100*_users_agg.csv`、アクティブ60ユーザ)で
  `dominant_ttft_bottleneck` が"communication"になった人は両条件とも0/60。

distance(ユーザ-GPU間距離)と`communication_latency_ns`の相関係数は両条件とも
約0.11〜0.12で、ほぼ無相関。

### RTT列のさらなる内訳: 伝搬遅延(純粋な距離由来RTT) vs シリアライズ時間

上表の「RTT」列はuplink/downlinkの合計(伝搬遅延+シリアライズ時間)。この
うち純粋に距離(平均276m)由来の伝搬遅延がどれだけを占めるかを分解すると:

| 内訳 | 100Mbps | 10Mbps |
| --- | ---: | ---: |
| 距離由来の伝搬遅延(up+down合計、平均距離276m) | 2.76us | 2.76us(**帯域に非依存で一定**) |
| ペイロードのシリアライズ時間(up+down合計) | 330.5us | 3304.7us |
| **純粋な伝搬遅延がRTT列に占める割合** | **0.83%** | **0.08%** |

帯域を下げると純粋な伝搬遅延の相対的な存在感はむしろ**小さくなる**
(0.83%→0.08%)。これは、伝搬遅延が「距離×伝搬遅延係数(5ns/m)」のみで
決まる固定値(今回の距離レンジ32〜519mでは往復高々数us)であるのに対し、
シリアライズ時間は「ペイロードサイズ÷帯域」で決まり帯域低下にそのまま
反比例して伸びるため。

## 結論

1. **今回の設定（1km四方、都市内〜キャンパス内相当の距離、2GPU・100リクエスト）では、
   地理的距離によるRTTはTTFTに対して常に無視できるレベル**（RTT列自体が
   TTFTの0.48%〜4.62%、そのうち純粋な伝搬遅延成分はさらに0.08%〜0.83%のみ）。
2. 帯域をモバイル回線相当（10Mbps）まで下げても、communicationの絶対値は伸びるが、
   それは「距離によるRTT」ではなく「帯域が細いことによるペイロードのシリアライズ時間」
   が原因。RTT成分自体は帯域に依存せず一定。
3. TTFTの支配要因は一貫して prefill_service（約75〜80%）と queueing（約20%）であり、
   通信要因（RTT・シリアライズとも）がボトルネックになったユーザは0/60。
4. 前段で見つかった「プロファイルCSV単純合計とシミュレータ実測値の約2.4倍のギャップ」
   は、本実験の結果から見ても地理的RTTでは説明できない。原因は引き続き
   ASTRA-Sim解析バックエンド側のイテレーション粒度・メモリ/ネットワークモデルなど
   別のところにあると考えられ、未解明のまま。

## 次に見るべきこと

- RTTが実際にボトルネックになる閾値を探すには、`--area-width-m`/`--area-height-m` を
  数十〜数百km規模（WAN/衛星回線シナリオ）に広げるか、帯域をさらに絞る（1Mbps未満）
  必要がある。
- 「プロファイルCSV単純合計 vs 実測prefill_service」のギャップの原因特定は、
  `--log-level DEBUG` 相当でイテレーション単位のトレースを見る追加調査が必要
  （今回のセッションでは未着手）。
