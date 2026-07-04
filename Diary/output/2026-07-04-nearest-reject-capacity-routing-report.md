# 2026-07-04: NEAREST_REJECT(キャパ次第でreject→2番目に近いGPUへリダイレクト)の効果検証

日付: 2026-07-04
ブランチ: `experiment/sim-hack`
関連:
- `Diary/output/2026-07-03-geographic-rtt-communication-breakdown.md`(地理的RTTのTTFT分解、今回の発端)
- `Diary/code/2026-07-03-nearest-reject-capacity-routing.md`(本機能のコードレベル実装記録)

## 背景・動機

前段の地理分散実験(`2026-07-03-geographic-rtt-communication-breakdown.md`)で、
「地理的距離によるRTTはTTFTに対して無視できるレベル」という結論を得た。その際、
次のような疑問が出た:

> queueを待たずに空いているサーバへスケジュールするなら、そのサーバへのRTTは
> 大きくなるはず。近いサーバへのRTT + 別の(遠めの)サーバへのRTT、という設計に
> なるはずだが、それでも通信コストは無視できるのか?

この疑問に答えるため、「地理的な近さを最初に見た後、キャパ次第で受信をreject
し、2番目に近いサーバへスケジューリングし直す」というルーティングポリシー
`NEAREST_REJECT`を新規実装し(実装詳細は`Diary/code/`側参照)、実際にどんな
条件でこの戦略が効く/効かないかを検証した。

## 実装の要点(詳細は`Diary/code/2026-07-03-nearest-reject-capacity-routing.md`)

- 既存`NEAREST`ポリシーに対して、最近傍GPUの空きスロット
  (`running_reqs < max_num_seqs`、`scheduler.py`が元々使っている admission
  判定を流用)が無ければreject → 2番目に近いGPUへ確定的にリダイレクト
  (3番目以降へのカスケードなし)。
- rejectのコストは「容量チェックの往復・伝搬遅延のみ」(ペイロードのシリアライズ
  は含めない、軽量プローブという想定)。リダイレクト先への実際のリクエストは
  通常通りフルのuplink/downlink(伝搬遅延+シリアライズ時間)を払う。
- `workloads/generators/geographic.py`を拡張し、各ユーザの「2番目に近い
  GPU」(id・距離)をworkload JSONL/users CSVに追加出力するようにした。
- 出力CSVに`nearest_gpu_id`(本来の最近傍)/`rerouted`(0/1)/
  `reject_penalty_ns`の3列を追加。

## 実験1: 対称キャパシティ(両GPUとも`max-num-seqs=8`)

### セットアップ
- ワークロード: `workloads/generated/geo_2gpu100_reject_workload.jsonl`
  (2GPU・100リクエスト、ShareGPTベースLlama-3.1-8B、`--seed 42`。
  前回の`2026-07-03`地理実験と同一の送信内容・ユーザ/GPU配置に
  second-nearest情報を追加しただけ)
- クラスタ: `configs/cluster/single_node_multi_instance.json`
  (2 GPU, TP=1, `meta-llama/Llama-3.1-8B` on `RTXPRO6000`)
- 両GPUとも `--max-num-seqs 8`(CLIで一律指定、意図的にタイトな容量)

### 実行コマンド

```bash
# ベースライン: NEAREST(reject無し)
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_reject_workload.jsonl \
  --request-routing-policy NEAREST \
  --max-num-seqs 8 --num-req 100 \
  --output outputs/geo_2gpu100_nearest_cap8_run.csv \
  --run-id geo_2gpu100_nearest_cap8 --log-level WARNING \
  --geographic-user-output outputs/geo_2gpu100_nearest_cap8_users_agg.csv \
  --geographic-gpu-output outputs/geo_2gpu100_nearest_cap8_gpus_agg.csv \
  --geographic-metadata-output outputs/geo_2gpu100_nearest_cap8_run_metadata.json \
  --geographic-users-csv workloads/generated/geo_2gpu100_reject_users.csv \
  --geographic-gpus-csv workloads/generated/geo_2gpu100_reject_gpus.csv"

# NEAREST_REJECT: 同じキャパ制約下
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_reject_workload.jsonl \
  --request-routing-policy NEAREST_REJECT \
  --max-num-seqs 8 --num-req 100 \
  --output outputs/geo_2gpu100_nearestreject_cap8_run.csv \
  --run-id geo_2gpu100_nearestreject_cap8 --log-level WARNING \
  --geographic-user-output outputs/geo_2gpu100_nearestreject_cap8_users_agg.csv \
  --geographic-gpu-output outputs/geo_2gpu100_nearestreject_cap8_gpus_agg.csv \
  --geographic-metadata-output outputs/geo_2gpu100_nearestreject_cap8_run_metadata.json \
  --geographic-users-csv workloads/generated/geo_2gpu100_reject_users.csv \
  --geographic-gpus-csv workloads/generated/geo_2gpu100_reject_gpus.csv"
```

### 結果(全100リクエストで集計)

| 条件 | 平均TTFT | P99 TTFT | 平均Queue | Queue比率 | 平均Prefill | Prefill比率 | 平均RTT | RTT比率 | 平均総Latency | GPU0/GPU1件数|
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `NEAREST` | 18712ms | 43508ms | 18670ms | 99.8% | 42.3ms | 0.2% | 0.333ms | 0.00% | 27104ms | 49 / 51 |
| `NEAREST_REJECT` | 18685ms | 42187ms | 18638ms | 99.8% | 42.2ms | 0.2% | 0.338ms | 0.00% | 27073ms | 51 / 49 |

**両者はほぼ同一**(平均TTFT差0.15%、平均総Latency差0.11%)。`NEAREST_REJECT`
側は100件中**84件がreject＋リダイレクト**されたにもかかわらず、効果はほぼゼロ
だった。

### 分析: なぜ効かなかったか

request_idごとに`rerouted`フラグを追うと、明確な境界があった。

- request_id 0〜15(両GPU合計で`max-num-seqs=8`×2=16スロットを埋めるだけの
  最初の到着分): **reject 0件**
- request_id 16以降(84件): **reject 100%**

つまり到着16件目でシステム全体が飽和し、以後回復しない。両GPUが常に同時に
満杯なので、`NEAREST_REJECT`が「近い方が満杯なら2番目に近い方へ」と判断
しても、**リダイレクト先も同じくらい満杯**であり、行き先を変えるだけで
実質何も改善しなかった。

`--max-num-seqs 8`自体が構造的に不足していたことも定量的に確認できる。
1GPUあたり自然発生する約50リクエストが9秒間に到着し、平均出力長685
トークン(TPOT ~11〜25ms/token換算で1リクエストあたり約7.5〜17秒のGPU
占有)なので、定常的に捌くには8ではなく**60〜70スロット相当の同時実行容量
が必要**だった。8はその1/10以下で、対称構成では詰まることが最初から
確定していた設定。

reject自体のコスト(往復伝搬遅延のみ)は平均0.0027ms(rerouted分のみ)と
無視できるレベルで、これは設計通り。

## 実験2: 非対称キャパシティ(GPU0=8, GPU1=128)

実験1で「リダイレクト先に空きがなければ意味がない」ことが分かったため、
今度は意図的に非対称な構成(片方が小さい容量の"edge"GPU、もう片方が余裕の
ある"cloud"GPU)を作った。

### セットアップ
- クラスタ: `configs/cluster/single_node_multi_instance_asym.json`(新規)。
  `single_node_multi_instance.json`をベースに、instance単位で
  `max_num_seqs`をJSON側に直接指定(`__main__.py`の既存の
  instance-levelオーバーライド機構 `instance.get("max_num_seqs",
  args.max_num_seqs)` をそのまま利用、コード変更なし)
  - instance 0 (GPU0): `max_num_seqs: 8`
  - instance 1 (GPU1): `max_num_seqs: 128`
- ワークロード・その他は実験1と同一(同じ`geo_2gpu100_reject_workload.jsonl`)
- CLIの`--max-num-seqs`は指定不要(JSON側の値が優先される)

### 実行コマンド

```bash
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance_asym.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_reject_workload.jsonl \
  --request-routing-policy NEAREST \
  --num-req 100 \
  --output outputs/geo_2gpu100_nearest_asym_run.csv \
  --run-id geo_2gpu100_nearest_asym --log-level WARNING \
  --geographic-user-output outputs/geo_2gpu100_nearest_asym_users_agg.csv \
  --geographic-gpu-output outputs/geo_2gpu100_nearest_asym_gpus_agg.csv \
  --geographic-metadata-output outputs/geo_2gpu100_nearest_asym_run_metadata.json \
  --geographic-users-csv workloads/generated/geo_2gpu100_reject_users.csv \
  --geographic-gpus-csv workloads/generated/geo_2gpu100_reject_gpus.csv"

docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance_asym.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_reject_workload.jsonl \
  --request-routing-policy NEAREST_REJECT \
  --num-req 100 \
  --output outputs/geo_2gpu100_nearestreject_asym_run.csv \
  --run-id geo_2gpu100_nearestreject_asym --log-level WARNING \
  --geographic-user-output outputs/geo_2gpu100_nearestreject_asym_users_agg.csv \
  --geographic-gpu-output outputs/geo_2gpu100_nearestreject_asym_gpus_agg.csv \
  --geographic-metadata-output outputs/geo_2gpu100_nearestreject_asym_run_metadata.json \
  --geographic-users-csv workloads/generated/geo_2gpu100_reject_users.csv \
  --geographic-gpus-csv workloads/generated/geo_2gpu100_reject_gpus.csv"
```

### 結果

| 条件 | 平均TTFT | P99 TTFT | 平均Queue | Queue比率 | 平均Prefill | Prefill比率 | 平均RTT | RTT比率 | 平均総Latency | GPU0/GPU1件数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `NEAREST` | 8791.6ms | 37602ms | 8745.9ms | 99.5% | 45.3ms | 0.5% | 0.333ms | 0.00% | 19160ms | 49 / 51 |
| `NEAREST_REJECT` | **85.1ms** | 227.8ms | 21.7ms | 25.5% | 60.8ms | 71.5% | 0.335ms | 0.39% | 15214ms | 12 / 88 |

**平均TTFTが8791.6ms→85.1msと約103倍改善**。100件中37件がreject＋
リダイレクトされ、GPU0の割当が49→12件に減り、その分GPU1が51→88件を
引き受けた。

GPU単位の内訳(`NEAREST_REJECT`側):

| instance | リクエスト数 | うちredirect流入 | 平均Queue | 平均TTFT | P99 TTFT |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 (max_num_seqs=8) | 12 | 0 | 10.9ms | 61.6ms | 177.5ms |
| 1 (max_num_seqs=128) | 88 | 37 | 23.1ms | 85.3ms | 230.6ms |

GPU1は自然発生分51件+リダイレクト37件=88件を追加で引き受けても、平均queue
23.1ms・平均TTFT85.3msと健全な水準を維持した(128スロットの余裕があるため)。
GPU0も12件のみに絞られ、平均queue 10.9msと健全。

reject自体のコストは今回も平均0.0027ms(rerouted分のみ)で無視できるレベル。

## 結論

1. **「queueを待たずキャパ次第で別サーバへ弾く」戦略の効果は、リダイレクト
   先に実際の空き容量があるかどうかで完全に決まる。** 対称・両方飽和
   (実験1)では効果ゼロ、非対称・片方に余裕あり(実験2)では平均TTFTが
   約100倍改善、という明確なコントラストが出た。
2. rejectそのもの(最近傍への容量チェック往復)のコストは、両実験を通じて
   平均0.003ms未満と一貫して無視できるレベルだった。「近いサーバへのRTT +
   別サーバへのRTT」という追加コストへの当初の懸念は、少なくとも今回の
   モデル化(容量チェックは伝搬遅延のみの軽量プローブ)の範囲では実証的に
   否定された。
3. 効果を左右するのは通信コストではなく、**システム全体の実効容量が需要に
   対して足りているか、かつリダイレクト先にその余剰があるか**という、
   純粋にキャパシティプランニングの問題だった。

## 次に見るべきこと

- 非対称度合い(GPU1のmax_num_seqsをどこまで下げても効果が保たれるか)の
  感度分析。
- リダイレクト先自体が枯渇する(GPU1もオーバーフローで飽和する)閾値の探索
  ―― 現状88/128でまだ余裕があるように見えるが、GPU0側の需要をさらに
  増やすとGPU1も飽和し実験1と同じ状態に戻るはず。
- reject判定のモデル化(伝搬遅延のみ vs フルペイロード往復)を変えた場合の
  感度確認。
