# 2026-07-05: Experiment 1 結果 — キューで待つ(方法A) vs GPU間バックボーンでリダイレクト(方法B)

日付: 2026-07-05
ブランチ: `experiment/sim-hack`
関連: `Diary/implementation/20260705_experiment_1.md`(要件・実装記録)、
`kondoFolder/experiment_1/`(生成物一式)

## この実験で実際に何を動かしたか

`Diary/implementation/20260705_experiment_1.md`には、後から詳細な要件定義書
(伝搬時間のみのネットワークモデル、GPU間リンクは本MVPでは0コスト、
output_tokens=1固定、FCFS・同時実行数1のGPUモデル、全UE同時刻送信、
左右領域に67:33で厳密配置、など)が追記されている。今回実際にシミュレーション
したのは、その理想化された仕様そのものではなく、**LLMServingSimが今すでに
持っている機能で近似した版**であることを先に明記しておく。両者の違いは
最後の節にまとめる。

### 実際のセットアップ

| 項目 | 実際の値 |
| --- | --- |
| GPU数 | 2台(`configs/cluster/single_node_multi_instance.json`、Llama-3.1-8B on RTXPRO6000、TP=1、両GPUとも`max_num_seqs=24`で同一) |
| エリア | 10km×10km、GPU配置は1行×2列グリッド: GPU0(2500m,5000m)、GPU1(7500m,5000m)。GPU間直線距離5000m |
| ユーザ | 100人、**エリア全体に一様ランダム配置**(左右領域に厳密分割ではない) |
| GPU0:GPU1への送信確率 | 2:1になるよう`--user-frequency-file`でユーザ単位に重み付け(最近傍GPUがGPU0のユーザ群とGPU1のユーザ群、それぞれの重み合計が2:1になるよう正規化)。**100件という有限サンプルでの実現値は56:44**(期待値67:33に対し統計的ノイズあり、大量試行では2:1に収束することは別途確認済み) |
| UE↔GPU通信 | 10Mbps固定 + 距離比例伝搬遅延(5ns/m)+ペイロードのシリアライズ時間(既存geographicジェネレータのモデルそのまま) |
| GPU↔GPUバックボーン(方法Bのみ) | **1Gbps(未実測プレースホルダ)**・距離5000m。伝搬遅延+シリアライズ時間の両方を計上(「本MVPでは0コスト」という後から追記された仕様とは異なる) |
| リクエスト内容 | ShareGPTベースLlama-3.1-8B、入力268〜3452トークン、出力516〜822トークン(既存の`geo_2gpu100_src.jsonl`をそのまま再利用。**output_tokens=1固定ではない**) |
| 到着パターン | 元データの送信時刻をそのまま使用、**0.05秒〜9.15秒に分散**(全員同時刻送信ではない) |
| GPU処理モデル | LLMServingSimの通常の継続バッチング(vLLM風)。**FCFS・同時実行数1ではなく、`max_num_seqs`件まで同時実行** |

### 実行コマンド(実際に使ったもの)

```bash
# 方法A: NEAREST(キューで待つ、リダイレクト無し)
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance.json \
  --dtype bfloat16 --block-size 16 \
  --dataset kondoFolder/experiment_1/geo_10km_2to1_workload.jsonl \
  --request-routing-policy NEAREST \
  --max-num-seqs 24 --num-req 100 \
  --output kondoFolder/experiment_1/run_nearest.csv \
  --run-id exp1_nearest --log-level WARNING \
  --geographic-user-output kondoFolder/experiment_1/run_nearest_users_agg.csv \
  --geographic-gpu-output kondoFolder/experiment_1/run_nearest_gpus_agg.csv \
  --geographic-metadata-output kondoFolder/experiment_1/run_nearest_metadata.json \
  --geographic-users-csv kondoFolder/experiment_1/geo_10km_2to1_users.csv \
  --geographic-gpus-csv kondoFolder/experiment_1/geo_10km_2to1_gpus.csv"

# 方法B: NEAREST_MIGRATE(キャパ超過時にGPU間バックボーンで動的リダイレクト)
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_multi_instance.json \
  --dtype bfloat16 --block-size 16 \
  --dataset kondoFolder/experiment_1/geo_10km_2to1_workload.jsonl \
  --request-routing-policy NEAREST_MIGRATE \
  --gpu-backbone-bandwidth-gbps 1.0 --gpu-backbone-distance-m 5000 \
  --max-num-seqs 24 --num-req 100 \
  --output kondoFolder/experiment_1/run_migrate.csv \
  --run-id exp1_migrate --log-level WARNING \
  --geographic-user-output kondoFolder/experiment_1/run_migrate_users_agg.csv \
  --geographic-gpu-output kondoFolder/experiment_1/run_migrate_gpus_agg.csv \
  --geographic-metadata-output kondoFolder/experiment_1/run_migrate_metadata.json \
  --geographic-users-csv kondoFolder/experiment_1/geo_10km_2to1_users.csv \
  --geographic-gpus-csv kondoFolder/experiment_1/geo_10km_2to1_gpus.csv"
```

両方とも100リクエスト完走(壁時計で各約7分40秒)。

## 結果

### 全体(100リクエストで集計、`kondoFolder/experiment_1/run_*.csv`より)

| 条件 | 平均TTFT | P99 TTFT | 平均Queue | Queue比率 | 平均Prefill | Prefill比率 | 平均RTT | RTT比率 | 平均総Latency | GPU0/GPU1件数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `NEAREST`(方法A) | 3583.8ms | 11916.6ms | 3527.5ms | 98.4% | 53.0ms | 1.5% | 3.33ms | 0.09% | 13913.3ms | 56 / 44 |
| `NEAREST_MIGRATE`(方法B) | 3593.8ms | 13600.9ms | 3535.5ms | 98.4% | 52.4ms | 1.5% | 3.37ms | 0.09% | 13914.3ms | 46 / 54 |

**平均TTFTはほぼ同一(3583.8ms→3593.8ms、+0.3%)。P99 TTFTはむしろ方法Bの方が悪化**
(11916.6ms→13600.9ms、+14.1%)。総リクエスト数の**54%(100件中54件)が
リダイレクトされた**にもかかわらず、これが実態。

### GPU単位の内訳

| | NEAREST(方法A) | | NEAREST_MIGRATE(方法B) | |
| --- | ---: | ---: | ---: | ---: |
| | GPU0 | GPU1 | GPU0 | GPU1 |
| リクエスト数 | 56 | 44 | 46 | 54 |
| リダイレクト流入数 | — | — | 22 | 32 |
| 平均Queue | 4059.9ms | 2849.8ms | 2454.1ms | 4456.7ms |
| 平均TTFT | 4105.9ms | 2911.8ms | 2500.6ms | 4514.2ms |
| P99 TTFT | 12597.2ms | 7042.8ms | 6338.2ms | 13758.6ms |

方法Aでは自然な2:1偏りの通りGPU0(56件)がGPU1(44件)より混雑していた
(平均Queue 4059.9ms vs 2849.8ms)。方法Bでは**GPU0とGPU1の立場が逆転**した:
GPU0から32件がGPU1へリダイレクトされ、GPU1から22件がGPU0へリダイレクトされた
結果、最終的な件数はGPU0=46、GPU1=54となり、**GPU1が新たなボトルネックになった**
(平均Queue 4456.7ms、P99 TTFT 13758.6ms — 方法AでのGPU0の数字より悪い)。

### リダイレクトのコスト自体は今回も無視できるレベル

`migration_latency_ns`(GPU間バックボーン転送、1Gbps・5km)の平均は
**0.056ms**(リダイレクトされた54件のみで集計)。設計通り、GPU間の実際の
通信コストはTTFTに対してほぼ影響していない。

## 考察

1. **今回の設定では、方法B(動的リダイレクト)は方法Aに対して実質的に
   ベネフィットが無かった**(平均TTFTはほぼ同値、P99はむしろ悪化)。
2. 理由は`2026-07-04`の実験(`Diary/output/2026-07-04-nearest-reject-capacity-routing-report.md`)
   と同じ構造: **両GPUの容量(`max_num_seqs=24`)が同一**で、かつどちらも
   自然発生トラフィック単体で見ても容量に対して過負荷(過去の試算で
   1GPUあたり60〜70同時実行相当が必要なところ24しかない)。そのため
   「リクエスト送信確率が2:1」という**トラフィックの偏り**だけでは、
   リダイレクト先(GPU1)に本当の意味での余剰キャパシティが生まれない。
3. 実際、リダイレクトは片方向ではなく**双方向に発生**した(GPU0→GPU1が32件、
   GPU1→GPU0が22件)。どちらのGPUも常にどちらかの瞬間に満杯になり得るため、
   「空いている方へ振る」という判断が、その場その場では正しくても、
   十分な量が積み重なると**転送先自体を新たに飽和させてしまう**(今回は
   GPU1がそれに該当し、方法AでのGPU0より悪い状態になった)。
4. GPU間バックボーン転送のコスト自体(平均0.056ms)は、`2026-07-04`の
   知見と同様、無視できるレベルのまま変わらない。効果を左右するのは
   一貫して通信コストではなく、**リダイレクト先に本当の空き容量があるか**
   という点。
5. `2026-07-04`の非対称**容量**実験(GPU0=8, GPU1=128)では平均TTFTが
   約100倍改善したのに対し、今回の非対称**トラフィック**実験
   (容量は同一、送信確率のみ2:1)では改善が見られなかった。この対比から、
   **「リダイレクトが効くかどうかを決めるのは容量の非対称性であって、
   トラフィックの非対称性だけでは不十分」**という、より具体的な結論が
   得られた。

## 今回のセットアップと、後から追記された要件定義書との相違点

`Diary/implementation/20260705_experiment_1.md`には本レポート作成後に、
より厳密な要件定義書(伝搬時間のみのネットワークモデル、GPU間リンクは
本MVPで0コスト、output_tokens=1固定、FCFS・同時実行数1のGPUモデル、
全UE同時刻送信、左右領域へ67:33の厳密配置、τによる3方式比較など)が
追記されている。今回のシミュレーションは、その仕様の直接実装ではなく、
LLMServingSimの既存機能(継続バッチング、bandwidth+距離のネットワークモデル、
ShareGPT由来の可変長入出力、時間分散した到着)を使った近似版である。
主な相違点:

| 項目 | 後から追記された要件定義書 | 今回実際に動かしたもの |
| --- | --- | --- |
| ネットワークモデル | 伝搬遅延のみ(シリアライズ時間は無視) | 伝搬遅延+ペイロードのシリアライズ時間 |
| GPU間リンク(MVP) | コスト0 | 1Gbps・5kmで実コスト計上 |
| 出力トークン数 | 1固定(TTFTのみ評価) | ShareGPT由来の可変長(516〜822) |
| 到着タイミング | 全UE同時刻 | 0.05〜9.15秒に分散 |
| GPU処理モデル | FCFS・同時実行数1 | 継続バッチング(`max_num_seqs=24`まで同時実行) |
| ユーザ配置 | 左右領域に67:33で厳密配置 | エリア全体に一様配置+送信確率の重み付けで2:1(実現値56:44) |
| リダイレクト方式 | τ(タイムアウト)で3方式(即時/待ってから/無限待ち)を比較 | 容量超過を検知した瞬間に即座にリダイレクト(τ=0相当の1方式のみ) |

要件定義書の仕様(特にFCFS・同時実行数1・output_tokens=1)を厳密に再現するには、
LLMServingSimの継続バッチングスケジューラとは別の、より単純な専用シミュレータ
(要件定義書が最初から想定していた「軽量な離散イベントシミュレータ」)を
別途実装する方が素直な可能性が高い。この差分をどう扱うか(LLMServingSimを
さらに改造してこの仕様に寄せるか、要件定義書通りの軽量シミュレータを
別途作るか)は次の判断ポイントとして残す。
