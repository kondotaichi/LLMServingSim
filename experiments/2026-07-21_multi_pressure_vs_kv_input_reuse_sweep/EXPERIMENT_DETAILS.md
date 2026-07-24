# Multi-pressure vs KV-migrate: 入力長 × reuse率スイープ

## 0. 背景・目的

前日の`kondoFolder/diary/2026-07-20-formulate-routing-hypothesis-validation.md`
で、「全GPUを候補にする多候補redirectポリシー(Multi-candidate)」が
90秒・6000トークンの1ワークロードで、それまで最良だった単純な
`NEAREST_MIGRATE_KV`に勝ることを確認していた。ただし日記には
「現時点の最有力はMulti-candidateだが、固定APN条件の1 workloadであり、
seedと負荷条件の追加検証が必要である」と明記されており、本実験は
その追加検証にあたる。

具体的には、多候補方式の中でも**KV occupancy率(capacity pressure)が
最小の候補を選ぶ変種**(`min_pressure`セレクタ)を取り上げ、単純な
`NEAREST_MIGRATE_KV`との比較を、**入力長**と**KV prefix reuse率**を
振った3×3=9条件で行い、優位性が負荷条件(プロンプトサイズ・キャッシュ
再利用率)に依存するかどうかを検証する。

## 1. セットアップ

- モデル: `meta-llama/Llama-3.1-8B`、ハードウェア: RTX4090
- クラスタ: `configs/cluster/ten_node_rtx4090_apn.json`
  (10ノード×1インスタンス、`tp_size=1`、NPUメモリ24GB、
  link_bw=16、link_latency=20000ns)。10 GPUインスタンス、20ユーザー。
- スケジューラ: `max_num_seqs=128`、`max_num_batched_tokens=2048`、
  block size 16トークン。Prefix caching(xPU-Onlyスキーム)・chunked
  prefillは有効、centralized prefix caching・PIM offload・
  sub-batch interleaving・prioritize-prefillは無効。
- ネットワーク: `fixed_throughput_distance_proportional`
  (競合・キューイング・ジッタ・パケットロス・モビリティは全て無効 =
  決定論的な距離比例コストのみのAPN設定)。
- 各条件300リクエスト、90秒の到着ウィンドウ。
- git commit: `3f7a6247cff1b4a2c6e21ed8a10ae9d413d7e905`(各`metadata.json`に記録)。

## 2. データセットの出自

本実験ではワークロードを新規生成していない。
`experiments/2026-07-14_input_reuse_90s_sweep/workloads/*.jsonl`
(各300行)をそのまま再利用している。さらに遡ると、10000トークン系列は
`experiments/2026-07-14_input_reuse_90s_sweep/scripts/prepare_input10000_workloads.py`
により、`sharegpt_300_prompt6000_reuse50_90s.jsonl`(ShareGPT由来、90秒
到着、"cell_apn"地理生成器によるuser/gpu座標付き)を種として、決定論的な
疑似乱数で追加トークンidを延長したもの。各行は`input_toks`,
`reuse_prefix_toks`(16トークンブロック境界に丸め済み)、
`assigned_instance_id`(最寄りGPU)、`second_nearest_gpu_id`、地理・通信
関連フィールドを持つ。

## 3. スイープ条件(3×3、9条件)

| 軸 | 値 |
|---|---|
| 入力トークン数 | 512 / 2000 / 10000 |
| prefix reuse率 | 0.0 / 0.25 / 0.5(フォルダ名は`reuse00`/`reuse025`/`reuse05`) |

条件名: `input512_reuse00`, `input512_reuse025`, `input512_reuse05`,
`input2000_reuse00`, `input2000_reuse025`, `input2000_reuse05`,
`input10000_reuse00`, `input10000_reuse025`, `input10000_reuse05`。

## 4. 比較した2ポリシー(9条件 × 2ポリシー = 18実行)

このフォルダには専用の`run_*.sh`は無く、各ログのヘッダ(例:
`Run ID: multi-pressure-vs-kv-input10000_reuse00-NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE`)
から、`python -m serving`を条件×ポリシーごとに直接実行したことが分かる。

- **KV migrate(`NEAREST_MIGRATE_KV`)**: リクエストは常に最寄りGPUを
  試す。空きが無ければ、home または(唯一の)2番目に近いGPUのどちらかが
  空くまで再チェックを続け、空いた方へKVキャッシュごと転送する。
  3台目以降のGPUは一切考慮しない。
- **Multi learned(`NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE`)**:
  homeが受理不可の場合、クラスタ内の**現在admissibleな全インスタンス**
  を評価し、`capacity_pressure =
  (projected_active_kv_bytes + required_kv_bytes) / kv_budget_bytes`
  (リクエストを仮に受理した後のKVキャッシュ占有率)が最小のものを
  選択(同点はinstance idで解消)。選択先でKV予約(`KV_RESERVE`)を行い、
  リクエスト+KVを転送する。TTFT予測式でランキングする姉妹ポリシー
  (`..._MULTI_FORMULA_KV_RESERVE`)や待機数最小で選ぶ
  (`..._MULTI_WAITING_...`)とは異なり、この`min_pressure`変種は
  **純粋にKVメモリ占有率だけでクラスタ全体を負荷分散する**。

両ポリシーとも、07-20の日記で修正した「一度localと判定しても、状況が
変われば再評価する」動的再評価ロジックの上に構築されている。

## 5. 分析スクリプト `scripts/analyze_sweep.py`

1本のスクリプトで完結。

- `load_summary()`: 9条件それぞれについて両ポリシーの`requests.csv`を
  読み込み、`e2e_ttft_ns`→ms変換の上で、mean/p50/p95/p99 TTFT、両ポリシー
  の改善率(`(kv-multi)/kv*100`)、リクエスト単位差分による
  better/worse/same件数、`rerouted==1`のredirect数、平均`communication_ms`
  / `router_wait_ms`(`router_capacity_wait_ns`) / `scheduler_queue_ms`
  (`queueing_before_ttft_ns`) / `prefill_ms` / `other_ms`(残差)を計算し、
  `analysis/condition_summary.csv`と`analysis/mean_ttft_breakdown.csv`
  に出力。
- `breakdown(frame)`: E2E TTFTを Router queue(残差、下限0クリップ) /
  Scheduler queue / KV transfer / Compute-prefill / RTT-other comm
  (`communication_latency_ns - kv_migration_latency_ns`、下限0) / Total
  の5要素+合計に分解。
- `make_workload_breakdown_figures()`: 各条件について、全件 /
  redirectのみ / 非redirectのみ の3集団でKV migrate対Multi learnedの
  breakdownを計算し、1×3の横積み棒グラフ
  (`figures/ttft_breakdown_by_workload/<condition>.png`)を生成、
  合計msと件数`n=`を注記。**このグラフは
  `experiments/2026-07-21_mixed_workload_model_value/figures/ttft_breakdown_kv_vs_learned/input_reuse_sweep/`
  にも複製されており(ディスク上で確認済み)、この2つの実験が図を共有
  している。** 詳細な行データは`analysis/ttft_breakdown_by_workload.csv`
  (9条件×最大2ポリシー×最大3集団、空集団を除く)。
- `make_figures()`: `figures/improvement_heatmaps.png`
  (Mean/p95/p99改善率を入力長×reuse率グリッドでヒートマップ表示)、
  `figures/input10000_breakdown.png`(10000トークン条件のみ、reuse率×
  ポリシー別に`communication_ms`/`router_wait_ms`/`scheduler_queue_ms`/
  `prefill_ms`の積み上げ棒グラフ)。
- `write_report()`: `report.md`自体をこのスクリプトが結果表と説明文
  つきで自動生成している(手書きではなくビルド成果物)。

## 6. 出力ファイルの形

- `analysis/condition_summary.csv` — 9行。列:
  `condition, input_tokens, reuse, kv_mean_ms, multi_mean_ms,
  mean_improvement_pct, kv_p50_ms, multi_p50_ms, p50_improvement_pct,
  kv_p95_ms, multi_p95_ms, p95_improvement_pct, kv_p99_ms, multi_p99_ms,
  p99_improvement_pct, better_requests, worse_requests, same_requests,
  kv_redirects, multi_redirects`
- `analysis/mean_ttft_breakdown.csv` — 18行(9条件×2ポリシー)
- `analysis/ttft_breakdown_by_workload.csv` — 42データ行(長形式)
- `results/<condition>/<POLICY>/requests.csv` — 300行×170列
  (`e2e_ttft_ns`, `rerouted`, `router_capacity_wait_ns`,
  `queueing_before_ttft_ns`, `prefill_service_ns`,
  `communication_latency_ns`, `kv_migration_latency_ns`,
  `router_decision_instance_id`, capacity_pressure関連列など)
  + `gpus.csv`, `users.csv`, `metadata.json`
- `logs/<condition>_<POLICY>.log` — シミュレータの全標準出力

## 7. 結果(`report.md` / `condition_summary.csv`より)

| 条件 | Mean KV | Mean Multi | Mean改善 | p50改善 | p95改善 | p99改善 | Redirects KV→Multi |
|---|---:|---:|---:|---:|---:|---:|---:|
| input512_reuse00 | 65.9 ms | 65.9 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input512_reuse025 | 53.5 ms | 53.5 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input512_reuse05 | 40.7 ms | 40.7 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input2000_reuse00 | 233.9 ms | 233.9 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input2000_reuse025 | 180.2 ms | 180.2 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input2000_reuse05 | 124.0 ms | 124.0 ms | 0.0% | 0.0% | 0.0% | 0.0% | 0→0 |
| input10000_reuse00 | 18036.4 ms | 14773.9 ms | **18.1%** | −20.7% | **52.6%** | **66.7%** | 93→231 |
| input10000_reuse025 | 16162.0 ms | 12012.2 ms | **25.7%** | −39.4% | **63.7%** | **75.9%** | 99→219 |
| input10000_reuse05 | 10835.0 ms | 8614.9 ms | **20.5%** | −53.3% | **54.5%** | **61.7%** | 91→211 |

## 8. 結論・メカニズム

- **512/2000トークン条件は完全に同一結果**(redirect 0件、差分0.0%)。
  このプロンプトサイズと到着トレースではクラスタが容量制約に達せず、
  多候補ロジック自体が発火しない。
- **10000トークン(過負荷レジーム、KV migrateのmean TTFTが既に
  10.8〜18.0秒)でのみ乖離**: mean改善18.1〜25.7%、p99改善61.7〜75.9%
  だが、**p50は全条件で20.7〜53.3%悪化**。個別リクエストの
  worse件数(127〜150)はbetter件数(104〜119)を上回っており、
  **少数の極端なテール改善が平均を押し下げている一方、大半のリクエストは
  個別には多少悪化している**。
- 10000トークン3条件平均でmean改善約21.4%、p99改善約68.1%。
- **メカニズム**: KV migrateはredirect先をごく少数のGPUに集中させる
  (例: `input10000_reuse00`では93件中34件が1インスタンスに集中、最小1件)
  ため待機が発生する。Multi-pressureはKV占有率最小のGPUへ送るため、
  231件のredirectが10インスタンスにほぼ均等(21〜25件ずつ)に分散する。
  これはTTFT要素分解での`router_wait_ms`の低下
  (`reuse00`: 約16,585ms→13,354ms、`reuse025`: 約14,920ms→10,599ms、
  `reuse05`も同様)と整合し、`prefill_ms`や`communication_ms`はほぼ
  変わらない。
- redirect自体の件数はMulti-pressureで**倍以上に増える**(93→231,
  99→219, 91→211): クラスタのどこかに必ずadmissibleなGPUを見つけられる
  ため、(2番目に近いGPUが空くのを待つしかない)KV-migrateより遥かに
  頻繁にredirectする。「redirect頻度を上げてでも均等分散し、テール
  待機を減らす」というトレードオフになっている。

## 9. 限界・次の課題(report.md記載のスコープ)

結果が支持するのは「固定APN・90秒・この特定の到着トレース」における
過負荷耐性のみ。**低負荷条件・異なる到着seed・距離依存(非固定APN)の
ネットワークモデルでの優位性はまだ示されていない**。07-20日記が提起した
汎化性の問いには部分的にしか答えられておらず、「多候補ルーティングは
テール/過負荷レジームでのみ有効で、普遍的に有効というわけではない」
というのが現時点の結論。
