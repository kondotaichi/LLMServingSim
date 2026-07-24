# 混合ワークロードにおける学習モデルの価値検証

## 0. 目的・仮説

これまでの実験(入力長固定、prefix reuse率固定)では「単純なcapacity
ヒューリスティックで十分」という結果が出ていた。本実験はその条件を崩し、
**1回のシミュレーション実行の中で入力長・reuse率・トラフィックの山(通常/
バースト)を混在させた**ワークロードを作り、その上で「オフライン学習した
TTFT予測モデル(learned model)」が

- (a) KVを引き継いだ最寄りGPUへのredirect(`NEAREST_MIGRATE_KV`)
- (b) 学習モデルを使わない多候補ヒューリスティック(`NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`)

の両方に対して本当に価値を持つかを検証することが目的。

## 1. ワークロード設計

- **元データ**: `experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl`
  (300件)を再利用。ユーザ/GPU割当・地理情報・ネットワークスループット・
  出力トークン数は元データを維持し、入力トークンだけ目的の長さに切り詰め、
  到着時刻・request ID・payloadバイト数・reuseトークン数を再生成する。
- **生成スクリプト**: `scripts/prepare_workloads.py` → `workloads/`配下に
  6本のJSONLを生成。

### 6ワークロードの内訳

| 軸 | 値 |
|---|---|
| 到着率 | 2.5 rps(seed 1/2/3, 120.0秒) / 3.33 rps(seed 1/2/3, 90.09秒) |
| リクエスト数 | 各300件 |
| 入力トークン数(内部混在) | 512 / 2000 / 4000 / 6000 / 8000 / 10000(各50件) |
| prefix reuse率(内部混在) | 0 / 0.25 / 0.5(各100件、16トークンブロック境界に丸め) |
| トラフィック相(内部混在) | normal 240件 / burst 60件 |

- **burstの作り方**: インデックス窓 `[75,105)` と `[195,225)` の計60件を、
  相ごとに異なる指数分布からburst想定レート(通常想定レートの3倍)で生成し、
  実現平均レートがちょうど2.5または3.33 rpsになるよう全体を再スケール。
  実現値は`configs/workload_manifest.csv`に記録(例: `mixed_rate3p33_seed3`
  はburst実現レート10.13 rpsで最大)。
- 割当のシャッフルはseedごとに独立したRNG(`seed*1_000_003 + round(rate*1000)`)
  で行い、相ごとの入力長・reuse率の周辺分布は完全に一様に保たれる。

## 2. 比較した3ポリシー(3ポリシー × 6ワークロード = 18実行、`run_three_policy.sh`)

| ラベル | `--request-routing-policy` | 学習モデル | 挙動 |
|---|---|---|---|
| KV migrate | `NEAREST_MIGRATE_KV` | なし | Homeが詰まっていれば2番目に近いGPUへKV引き継ぎredirect |
| Multi no model | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | なし | 全admissible GPUを探索し、capacity pressure最小の候補へ即redirect |
| Multi learned | `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE` | あり | 同じ多候補探索だが、ローカル待機かredirectかの判断とcandidateランキングをオフラインTTFT予測式(`OfflineTtftFormula`)で行う |

共通CLIフラグ: `--cluster-config configs/cluster/ten_node_rtx4090_apn.json
--num-reqs 300 --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128
--max-num-batched-tokens 2048 --enable-chunked-prefill --enable-prefix-caching
--gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500
--kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9
--oneshot-redirect-margin-ns 200000000 --oneshot-max-local-wait-ns 1000000000`。
`MAX_PARALLEL=3`、`SKIP_COMPLETED=1`で冪等実行。クラスタは10 GPU
(RTX4090)・20ユーザー、モデルは`meta-llama/Llama-3.1-8B`。

## 3. 分析スクリプト `scripts/analyze_three_policy.py`

18本の`requests.csv`を読み込み、各ワークロードJSONLの
`mixed_traffic_phase`/`mixed_reuse_ratio`列と結合して以下を出力:

- `run_summary.csv` — (条件, ポリシー)ごとのTTFT分布(mean/p50/p95/p99/max)、
  redirect数、router待機/scheduler queue/prefillの平均。
- `pooled_rate_summary.csv` — 同じ指標をseed3本分プールしたもの(各900件)。
- `subgroup_summary.csv` — 相(normal/burst)・入力長・reuse率別に67行。
- `learned_vs_no_model_paired.csv` — 「Multi learned − Multi no model」の
  リクエスト単位ペア比較(平均差分、better/worse/same件数、GPU選択が
  異なった件数、redirect数)。
- `learned_redirect_prediction_accuracy.csv` — learnedポリシーでredirect
  された全リクエストについて、実測`e2e_ttft_ns`と
  `oneshot_predicted_redirect_ttft_ns`の誤差(MAE・bias・中央値・p90/p95/p99)。
- `ttft_breakdown_by_redirect_status.csv` / `ttft_breakdown_by_workload.csv` /
  `ttft_breakdown_kv_vs_learned.csv` — Router queue / Scheduler queue /
  KV transfer / Compute-prefill / RTT-other comm / Totalの5要素分解を、
  全件・redirectのみ・非redirectのみで、レート別・ワークロード別・
  KV対learnedの2ポリシー比較別に算出。
- GPU使用率分析(`make_gpu_utilization_outputs()`)は関数として存在するが、
  **この実験のgpus.csvには`utilization_pct`/`busy_time_ns`列がまだ無く、
  現状は出力が生成されない**(計装追加後に別途再実行が必要)。

### 生成された図

`rate_level_performance.png`(レート×ポリシー別TTFT棒グラフ)、
`seed_consistency.png`(seedごとの平均TTFT推移)、
`learned_value_by_input.png`(レート×入力長のlearned−no-model差分ヒートマップ)、
`figures/ttft_breakdown_rate2p5.png` / `rate3p33.png`、
`figures/ttft_breakdown_by_workload/`(条件別6枚)、
`figures/ttft_breakdown_kv_vs_learned/`(条件別+プール2枚)。

なお `figures/ttft_breakdown_kv_vs_learned/input_reuse_sweep/` 配下の9枚は
このスクリプトからは一切参照されておらず、`2026-07-21_multi_pressure_vs_kv_input_reuse_sweep`
実験の`analyze_sweep.py`が書き込んだコピーが紛れ込んでいるもの
(仕様通りだが本実験のパイプライン出力ではない)。

## 4. 結果(`reports/final_three_policy_analysis.md`より)

### プール後(seed3本、各900件)

| レート | ポリシー | Mean | p50 | p95 | p99 | Max | Redirects |
|---:|---|---:|---:|---:|---:|---:|---:|
| 2.5 | KV migrate | 572.2 ms | 517.6 ms | 1332.4 ms | 1780.1 ms | 2491.9 ms | 12 |
| 2.5 | Multi no model | 571.3 ms | 511.5 ms | 1327.7 ms | 1780.0 ms | 2491.9 ms | 12 |
| 2.5 | Multi learned | 570.8 ms | 506.2 ms | 1332.4 ms | 1807.1 ms | 2491.9 ms | 12 |
| 3.33 | KV migrate | 716.4 ms | 550.3 ms | 1677.5 ms | 3294.2 ms | 13020.6 ms | 82 |
| 3.33 | Multi no model | 607.5 ms | 536.6 ms | 1353.6 ms | 1885.2 ms | 3193.4 ms | 55 |
| 3.33 | Multi learned | 617.0 ms | 539.5 ms | 1374.8 ms | 1891.3 ms | 3193.4 ms | 58 |

3.33 rpsではno-model MultiがKV migrateに対し **mean 15.2%・p95 19.3%・
p99 42.8%** 改善。一方でlearned MultiはKV migrateよりは良いが、
no-model Multiより **meanで9.5ms遅く**、p50/p95/p99でも劣る。

### seed一貫性(learned − no-model 平均TTFT)

- 2.5 rps: −1.49 / −0.09 / +0.01 ms(無視できる程度、方向も不安定)
- 3.33 rps: +2.23 / +14.70 / +11.49 ms(**全seedでlearnedが一貫して悪化**)

### 相・入力長別

- normal/burst: 2.5rpsではburst時のみlearnedがわずかに有利(−7.3ms)だが
  normalでは不利(+1.1ms)。3.33rpsはnormal(+7.6ms)・burst(+16.9ms)
  とも悪化 — burstでの優位性は高負荷には汎化しない。
- 入力長別(learned−no-model, 3.33rps): 512→+11.0, 2000→+0.7, 4000→−1.1,
  6000→+17.5, 8000→+15.0, 10000→+13.7 — ほぼ全ての入力長でlearnedが悪化。

### Redirect挙動と予測精度

学習モデルによるredirectは全て`predicted_local_wait_exceeds_limit`が
トリガー(Homeが詰まった時点で常にローカル待機の上限予測を超える=
ゲートが「飽和」しており、待つかredirectするかの繊細な判断はできず、
候補ランキングにしか効いていない)。70件のlearned redirectの予測誤差は
MAE **127.3ms**、bias +28.3ms、p95絶対誤差 **497.8ms**、p99 **677.9ms** —
この誤差の大きさが、3.33rpsの混雑下でランキングの利点を打ち消している。

## 5. 結論(report.mdより)

1. 全admissible GPUを探索する多候補方式自体は有効 — 3.33rpsでの
   KV-migrateの深刻なテールを解消する。
2. **学習なしのcapacity pressure最小選択は強力なヒューリスティック** —
   持続負荷でもburstでも学習モデルに勝つ。予測誤差由来のアーティファクトも無い。
3. 現行のオフラインTTFT式は、1回の実行内で入力長/reuse率が混在する
   状況に汎化していない。均質シナリオで学習されており、1秒ローカル待機
   ゲートがブロック時に常に飽和してしまう。

**推奨**: 次のモデル改良では、混在ワークロードで学習し、絶対TTFTではなく
候補間の相対regretを予測し、将来負荷/予約状況を表す特徴量を追加すべき。
それまでは **`NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`(学習モデル無し)
をデフォルト方策として推奨** — 現状、学習モデルは価値を追加しておらず、
高負荷下ではむしろ悪化させている。

## 6. 補足: GPU使用率計装

report.mdの最終セクションには、シミュレータに`busy_time_ns` /
`idle_time_ns` / `utilization_pct` / `completed_batch_count`を
`gpus.csv`に記録する拡張を行った旨が書かれているが、**この実験の
18件の結果はその計装より前に生成されたもの**であり(実際の`gpus.csv`に
`utilization_pct`列は無いことを確認済み)、`gpu_utilization_by_run_gpu.csv`
等の出力はまだ存在しない。利用するには再実行が必要。
