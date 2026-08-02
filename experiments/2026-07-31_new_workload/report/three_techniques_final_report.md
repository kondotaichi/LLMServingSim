# TTFT改善3手法 最終レポート: APN活用 / Pipeline Parallel / 投機的KVキャッシュ移送

## 0. 背景と対象範囲

地理分散GPUクラスタでのLLMサービングにおいて、容量逼迫時に発生するリクエストの
redirect(他GPUへの転送)がTTFT(Time To First Token)に与えるコストをどう
下げるか、という一連の実験の最終まとめ。

検証した4手法のうち、**Method A(scheduler待ち隠蔽)は効果が確認できず不採用**
とした(理由は§4)。採用する3手法は以下:

1. **APN(高スループット環境)の活用** — redirect/KV移送そのものを可能にする高帯域
   バックボーンの導入
2. **Pipeline Parallel(PP2)** — redirectの発生頻度自体を下げる
3. **投機的KVキャッシュ移送(Method C)** — 発生したredirectのコストを実コンテンツ
   再利用で下げる

すべてPP1(独立10GPU)構成、`meta-llama/Llama-3.1-8B`、RTX4090プロファイルでの
検証結果。

---

## 1. 結論サマリ

| 手法 | 効きどころ | 実測効果 | 総合評価 |
|---|---|---|---|
| **APN活用(redirect可否)** | 容量逼迫時に他GPUへ逃がせるか | **-76.5%**(input6000, 18/300件のredirectのみで全体を救う) | ◎ 最も効果が大きく、他の2手法の前提条件でもある |
| **Pipeline Parallel(PP2)** | redirectの発生頻度そのものを減らす | **-20〜-43%**(redirect圧が強いワークロードほど大) | ◎ 実装リスクが低く効果も大きい |
| **投機的KVキャッシュ移送(Method C)** | 発生したredirectのKV転送コストを削減 | 全体**-4.6%**、redirectのみ**-6.4%**(本物のセッション継続データ) | ○ 本物のコンテンツ再利用がある場合に明確に有効 |

3手法は競合せず、**積み上げて併用できる**関係にある(APNが土台、PP2が頻度を
下げ、Method Cが残ったredirectのコストを下げる)。

---

## 2. APN(高スループット環境)の活用

### 何を検証したか
GPU間を高帯域バックボーン(APN、10.7 Gbit/s + KVステージング33.8 GB/s)で
接続し、容量逼迫時に他GPUへredirectできる構成(`NEAREST_CAPACITY_MULTI_
PRESSURE_KV_RESERVE`)と、redirectを一切行わずホームGPUのみで処理する構成
(`NEAREST_KV`)を比較した。後者は「APNを活用しない(=容量逼迫時の
逃げ場がない)」場合の下限に相当する。

### 実測結果

| ワークロード | NEAREST_KV(redirectなし) | APN活用(容量ベースredirect) | 差分 | redirect数 |
|---|---:|---:|---:|---:|
| input2000_reuse025(軽負荷) | 180.2ms | 180.2ms | ±0% | 0/300 |
| input6000_reuse05(中〜高負荷) | **2004.1ms** | **470.5ms** | **-76.52%** | 18/300 |

軽負荷では両者に差がない(そもそも容量逼迫が起きないため)。しかし中〜高負荷
では、**わずか18件(6%)のredirectを可能にするだけで、全体平均TTFTが1/4に
まで改善**した。これはredirectされた18件自身の改善だけでなく、逃げ場が
できたことでホームGPU側の待ち行列全体が緩和される、システム全体への波及効果。

### 実行コマンド

```bash
# APNなし(redirect不可)のベースライン
python3 -m serving \
  --cluster-config configs/cluster/ten_node_rtx4090_apn.json --pp-size 1 \
  --dataset <workload>.jsonl --num-reqs 300 \
  --request-routing-policy NEAREST_KV \
  --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
  --graph-converter in-process --trace-io buffered \
  --output results/nearest_kv/requests.csv \
  --run-id nearest-kv-baseline --log-level WARNING

# APN活用(容量ベースredirect、推奨構成)
python3 -m serving \
  --cluster-config configs/cluster/ten_node_rtx4090_apn.json --pp-size 1 \
  --dataset <workload>.jsonl --num-reqs 300 \
  --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
  --graph-converter in-process --trace-io buffered \
  --output results/apn_redirect/requests.csv \
  --run-id apn-redirect-baseline --log-level WARNING
```

---

## 3. Pipeline Parallel(PP2)

### 何を検証したか
10台の独立GPU(PP1)を2台1組・5論理サーバのパイプライン並列(PP2)へ
組み替え、1論理サーバあたりの実効容量を増やすことで、単発の負荷スパイクでも
redirectが発生しにくくなるかを検証した。

### 実測結果(naive、容量ベースredirect構成での比較)

| ワークロード | PP1平均TTFT | PP2平均TTFT | 差分 | PP1 redirect率 | PP2 redirect率 |
|---|---:|---:|---:|---:|---:|
| input2000_reuse025 | 180.2ms | 179.6ms | -0.29% | - | - |
| input6000_reuse05 | 470.5ms | 455.5ms | -3.19% | - | - |
| input8000_reuse025 | 1675.6ms | 958.3ms | **-42.81%** | 61.0% | 11.7% |
| input8000_reuse05 | 805.0ms | 640.4ms | **-20.44%** | 35.3% | 4.0% |

効果の大きさは**PP1側でどれだけredirectが発生していたか**にほぼ比例する。
軽量ワークロードでは恩恵がほぼゼロだが、input8000系のような高負荷では
redirect率が61%→12%、35%→4%へ激減し、TTFTも半分以下になる。

### 実行コマンド

```bash
# PP2(5論理サーバ×2GPUパイプライン)
python3 -m serving \
  --cluster-config configs/cluster/five_node_rtx4090_apn.json --pp-size 2 \
  --dataset <PP2用workload>.jsonl --num-reqs 300 \
  --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
  --graph-converter in-process --trace-io buffered \
  --output results/pp2/requests.csv \
  --run-id pp2-baseline --log-level WARNING
```

PP2用ワークロードは`assigned_instance_id`/`second_nearest_gpu_id`が論理
サーバ番号(0〜4)を指すよう変換が必要(`experiments/2026-07-22_pp2_five_
workloads/scripts/prepare_workloads.py`参照)。PP1(10 GPU独立構成)は
`--cluster-config configs/cluster/ten_node_rtx4090_apn.json --pp-size 1`
のまま、変換不要の元ワークロードを使う。

---

## 4. 投機的KVキャッシュ移送(Method C)

### 何を検証したか
容量圧迫を検知したGPUの「頻出ユーザー上位K人」を特定し、彼らの直近の
コンテンツを、空いている別GPUへリクエストが来る前に先回りで移送しておく
(`--enable-proactive-kv-prewarm`)。実際に次のリクエストが来た時、内容が
一致していればKV転送コストの大部分を回避できる。

### 実測結果

初期の合成ワークロード(`reuse_prefix_toks`がトークン内容と無関係な合成
ラベルだった)では効果が限定的だったが、以下2つの改修を経て明確な改善を
確認した:

1. **行き先バインド修正**(`router.py`の`_proactive_pin_candidate`):
   先回り移送先とルーティング判断を一致させる。「行き先予測のズレ」による
   損失が79〜85%→11%へ低下。
2. **本物のセッション継続ワークロード**(`sharegpt.py`/`regional_ratio.py`
   修正): 同一ユーザーの複数リクエストが実際に会話履歴を引き継ぐ、本番
   相当のデータを構築。

| 条件 | 全体TTFT | redirectのみTTFT | ヒット率 |
|---|---:|---:|---:|
| 合成ワークロード(input8000系) | +0.25〜-3.0% | -2.8〜-13.5% | 3〜8% |
| **本物のセッション継続データ** | **-4.56%** | **-6.36%** | **8.2%** |

ヒットしたリクエスト単体では平均**-423ms/件**の削減。無駄撃ち(ヒットしな
かった投機)のコストは、投機コンテンツを優先evictする設計(`mark_
speculative`)によりほぼゼロ(±10ms以内)に抑えられている。

### 実行コマンド

```bash
python3 -m serving \
  --cluster-config configs/cluster/ten_node_rtx4090_apn.json --pp-size 1 \
  --dataset <本物のセッション継続workload>.jsonl --num-reqs 300 \
  --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
  --enable-proactive-kv-prewarm \
  --proactive-kv-prewarm-pressure-threshold 0.8 \
  --proactive-kv-prewarm-top-k 3 \
  --proactive-kv-prewarm-output results/method_c/proactive_migration_log.csv \
  --graph-converter in-process --trace-io buffered \
  --output results/method_c/requests.csv \
  --run-id method-c-baseline --log-level WARNING
```

`--proactive-kv-prewarm-pressure-threshold`はトポロジー依存(PP1は0.8、
PP2は0.6が最良、と閾値スイープで確認済み)。

### 本物のセッション継続ワークロードの作り方

Method Cが機能するには、同一ユーザーの連続リクエストが実際に文脈を
引き継いでいる必要がある。以下の3段階パイプラインで生成する
(いずれも本調査で`session_id`対応・ターン順序保証の修正を加えたもの):

```bash
# 1. 本物のマルチターンShareGPT会話を、深いターン(長い会話)中心に抽出
python3 -m workloads.generators sharegpt \
  --model meta-llama/Llama-3.1-8B \
  --source shibing624/sharegpt_gpt4 \
  --num-reqs 300 --sps 10 --seed 42 \
  --min-input-toks 6000 --max-input-toks 10000 --max-kv-toks 20000 \
  --max-sessions 0 \
  --output workloads/sharegpt_session.jsonl

# 2. 地域・ユーザーへの割当(同一セッション=同一ユーザーに固定)
python3 -m workloads.generators regional-ratio \
  --input workloads/sharegpt_session.jsonl \
  --ratios <地域別アクティビティ比率CSV> \
  --day-type weekday --users-per-region 35 --duration-seconds 60 --seed 42 \
  --output workloads/sharegpt_session_regional.jsonl

# 3. 地理配置(GPU座標・ユーザー座標)の付与
python3 -m workloads.generators cell-apn \
  --input workloads/sharegpt_session_regional.jsonl \
  --output workloads/sharegpt_session_cell_apn.jsonl \
  --users-output placements/users.csv \
  --gpus-output placements/gpus.csv \
  --metadata-output placements/metadata.json \
  --users-per-gpu 35 --kv-reuse-ratio 0.5 --seed 42
```

`--users-per-region`/`--users-per-gpu`は、生成したセッション数を上回る
値に設定すること(セッションの衝突を防ぐため。目安: セッション数を
地域数(10)で割った最大値+余裕)。

---

## 5. 不採用: Scheduler待ち隠蔽(Method A)

`--enable-scheduler-hide-kv-migration`は、あらゆる条件(合成/本物データ、
PP1/PP2、到着レート1.5x〜10xの輻輳スイープ、トークンサイズ2000〜8000)で
**効果が一貫して0.00%**だった。

原因は、redirect先選定ポリシー自体が「容量に余裕がある候補」を選ぶ設計に
なっているため、Method Aが隠そうとしている「KV転送後のscheduler待ち」が
構造的にほとんど発生しないこと。ルーティングポリシーを緩めて人為的に競合を
作ることも検討したが、それ自体がredirect品質を落とし本末転倒になるため
不採用とした。詳細な検証過程は
`experiments/2026-07-28_pp_schedule_conceal_and_speculative/reports/
three_techniques_summary.md`を参照。

---

## 6. 推奨構成(3手法併用)

```bash
python3 -m serving \
  --cluster-config configs/cluster/five_node_rtx4090_apn.json --pp-size 2 \
  --dataset <本物のセッション継続workload(PP2変換済み)>.jsonl --num-reqs 300 \
  --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
  --enable-proactive-kv-prewarm \
  --proactive-kv-prewarm-pressure-threshold 0.6 \
  --proactive-kv-prewarm-top-k 3 \
  --proactive-kv-prewarm-output results/combined/proactive_migration_log.csv \
  --graph-converter in-process --trace-io buffered \
  --output results/combined/requests.csv \
  --run-id combined-apn-pp2-methodc --log-level WARNING
```

- `--cluster-config five_node_rtx4090_apn.json --pp-size 2`: Pipeline Parallel
- `--request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`
  + `--gpu-backbone-bandwidth-gbps`/`--kv-staging-*`: APN活用(redirect有効化)
- `--enable-proactive-kv-prewarm`関連フラグ: 投機的KVキャッシュ移送

**PP2適用後にredirect自体が減った状態でMethod Cがどこまで上乗せで効くか
(3手法の組み合わせ効果)は本調査のスコープ外で、未検証。次の検証項目として
推奨する。**

---

## 7. 未検証・今後の課題

- PP2 + Method Cの組み合わせ効果の実測
- Method Cのヒット率向上(観測ウィンドウ拡張、より現実的なターン間隔モデル、
  大規模化) — 現状ヒット率8.2%、律速要因はユーザーの再訪検知漏れ(81%)
- ワークロード生成のターン間隔モデルが恣意的(最低500msクランプのみ)。
  実際のチャット返信間隔を模した分布への置き換えが望ましい
- 本レポートの検証はPP1構成中心。100-GPU都市ワークロード
  (`experiments/2026-07-31_new_workload/`)のような大規模構成での
  再検証は未実施
