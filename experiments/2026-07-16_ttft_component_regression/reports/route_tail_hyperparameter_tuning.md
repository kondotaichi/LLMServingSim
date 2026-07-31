# Route queue正値時間モデルのhyperparameter調整

## 背景: 優先順位の転換

これまでの`experiments/2026-07-21-add_gpu_utilization/MODEL_ITERATION_HISTORY.md`
での作業は「redirect候補群の中で最良のGPUを選べているか」という**順位**の
評価(counterfactualラベル、n=18)を主軸にしていた。しかし本来の用途は、
現在のGPUとredirect候補集合それぞれについてTTFTを事前に予測し、KV
キャッシュの投機的移送を判断することである。したがって、順位が合っている
かどうかより、**個々の予測値そのものがどれだけ正確か**(回帰としてのMAE/
R²)を優先して改善する方針に転換した。

## 現状の内訳: 誤差はrouteコンポーネントにほぼ全て集中

`analysis/ttft_formula/summary.json`(scenario-held-out CV、n=13,500、
15 scenarios)の内訳を洗い直すと、

| Component | MAE | 実測平均 | 予測平均 |
|---|---:|---:|---:|
| Router queue | 630.2 ms | 856.3 ms | 468.5 ms |
| Scheduler queue | 48.4 ms | 60.3 ms(推定) | 56.7 ms(推定) |
| Compute/prefill | 59.7 ms | 703.6 ms | 706.9 ms |
| **TTFT合計** | **714.6 ms** | — | — |

Router queue単体でTTFT全体のMAEの**88%**を占める。scheduler/computeは
予測平均と実測平均がほぼ一致しており、既に十分に較正されている。

さらにrouter queueのMAE 630.2msを分解すると、queueが実際には発生しな
かった85%のrequest(誤差合計16.9ms)はほぼ影響しておらず、**queueが
実際に発生した15%のrequestだけで613.3ms(97%)を占める**。この正値
subsetを実測値のdecileに分けると、次のような系統的な圧縮が見えた。

| 実測decile(ms) | 実測平均 | 予測平均 |
|---:|---:|---:|
| 0〜1 | 0.3 | 222.3(低い方を過大予測) |
| 3〜263 | 41.9 | 727.3(過大予測) |
| 1297〜2725 | 1935.0 | 2467.3(ほぼ整合) |
| 6412〜9831 | 8057.9 | 3534.6(過小予測) |
| 15358〜53046 | 24313.5 | 8975.3(**大幅に過小予測、比率0.37**) |

`route_probability`(発生確率、ROC-AUC 0.99995)は各decileで0.97〜0.99
とほぼ一定であり、分類器側は問題ではない。問題は正値時間を予測する
`GradientBoostingRegressor`(深さ2の木100本、`learning_rate=0.05`、
`loss=absolute_error`)側にあり、予測レンジが実測レンジより大きく
圧縮されている。

## Hyperparameter sweep

`scripts/tune_route_tail_model.py`を新規作成し、scheduler/compute/前処理
/event modelは一切変更せず、route正値時間の`GradientBoostingRegressor`
のhyperparameterだけを、既存のleave-one-scenario-out CVプロトコルの下で
掃引した(production artifactへは一切書き込まない読み取り専用検証)。

25バリアントを試した結果、`learning_rate`を0.05→0.15付近まで上げると
一貫して改善し、木の本数を100→60〜80へ減らすとさらに改善する、という
明確な単峰の最適領域が見えた(隣接値でなだらかに変化しており、CVノイズ
による偶然ではない)。

| バリアント | route MAE | route R² | top decile比率 |
|---|---:|---:|---:|
| 現行production(lr=0.05, n=100) | 630.2 ms | 0.456 | 0.369 |
| lr=0.15, n=100 | 618.2 ms | 0.507 | 0.515 |
| **lr=0.15, n=70** | **612.4 ms** | **0.511** | 0.489 |
| lr=0.15, n=60 | 612.0 ms | 0.510 | 0.475 |
| lr=0.25, n=100 | 643.1 ms | 0.452 | 0.523(過学習気味) |
| squared_error loss | 657.3 ms | 0.379 | 0.287(悪化) |
| depth=3 | 641.5 ms | 0.429 | 0.372(悪化) |

`n_estimators`を増やす(300本)、`max_depth`を増やす(3)、
`loss=squared_error`に変える、といった「単純に容量を増やす」方向は
軒並みoverall MAEを悪化させた(scenario-held-outでの汎化性能が落ちる)。
一方、**学習率を上げて木の本数を減らす**方向は、容量を増やさずに
既存の100本×0.05という組み合わせが単に学習不足(undertrained)だった
ことを示しており、副作用なく改善する。

参考として、2026-07-22時点の日誌でLightGBMへ全面切り替えた場合の
Router MAEが「630.2→612.3ms」と報告されていたが、今回の
`GradientBoostingRegressor`のhyperparameter調整だけで**ほぼ同じ
612.0〜612.4msに到達**した。新しいライブラリ依存を増やさずに同水準の
改善が得られることから、この特徴量集合でのroute tail回帰は概ね
天井に近い可能性が高く、これ以上の大幅な改善には新しい特徴量
(item 15で提案した候補GPU向け到着率特徴量など)が必要と考えられる。

## `learning_rate=0.15, n_estimators=70`採用時の全体効果

`scripts/fit_ttft_formula.py`に`--route-tail-n-estimators` /
`--route-tail-learning-rate` CLI引数を追加(デフォルトは現行production値
のまま、`--output-dir`と同じtrial実行パターン)し、scenario-held-out CV
全体を再実行して確認した。

| 指標 | 現行production | learning_rate=0.15, n_estimators=70 | 差分 |
|---|---:|---:|---:|
| route_mae_ms | 630.19 | 612.37 | −17.8 ms(−2.8%) |
| **ttft_mae_ms** | **714.64** | **697.58** | **−17.1 ms(−2.4%)** |
| **ttft_r2** | **0.4663** | **0.5203** | **+0.054(+11.6%)** |
| route_upper_residual_ms(90%ile安全マージン) | 10311.7 | 9720.6 | −591 ms |
| route_positive_upper_coverage | 0.8996 | 0.9001 | ほぼ同じ |

scheduler_mae_ms(48.41)とcompute_mae_ms(59.68)は変更していないため
不変。TTFT全体のR²が0.466→0.520まで改善し、routing判断用の90%ile
上側安全マージンも591ms縮小した(=同じ信頼度をより小さい補正で
達成できるようになった)。production artifactへはまだ反映していない。

## 残っている限界

- 予測レンジの圧縮は縮小したが解消はしていない(top decile比率
  0.369→0.489)。低deciles側の過大予測もほぼ変わらず残っている。
- この改善はroute tail回帰そのものの精度向上であり、item 15で確定した
  「決定時点では存在しない未来の到着による誤差」(相関0.803)は
  一切解消しない。両者は独立な誤差源である。
- `scripts/tune_route_tail_model.py`のsweepはPythonの`GradientBoostingRegressor`
  を直接使っており、`fit_ttft_formula.py`のevent modelを毎fold再学習して
  いるため、production版と1:1の設定(前処理、feature列)を保っているが、
  最終的な採否判断は本レポートの`fit_ttft_formula.py` trial実行結果
  (全パイプライン、production同一コード)を正とする。

## 事後較正(post-hoc calibration)の検証 — 3種とも不採用

上記のhyperparameter調整後も、実測decileごとの予測値は依然として系統的
(低deciles側は過大予測、高deciles側は過小予測)だったため、`GradientBoostingRegressor`
自体は変更せず、その出力に事後的な単調写像をかぶせるだけで追加改善できる
か検証した。`scripts/calibrate_route_tail_model.py`を新規作成し、outer
scenario(leave-one-scenario-out)ごとに、**その scenario を一度も使って
いない inner leave-one-scenario-out(残り14 scenarios内の再分割)**で
較正曲線を学習してから outer test へ適用する、nested CV構成にした
(item 9のguardrail λ選定と同じ「held-out requestに触れない」設計)。

| バリアント | route MAE | route R² | top decile比率 |
|---|---:|---:|---:|
| 較正なし(lr=0.15, n=70) | **612.4 ms** | 0.511 | 0.489 |
| Isotonic回帰(ms空間) | 691.1 ms(悪化) | **0.536**(改善) | **0.519**(改善) |
| Isotonic回帰(log空間) | 624.0 ms(悪化) | 0.463(悪化) | 0.380(悪化) |
| Binned-median較正(20 bins) | 631.6 ms(悪化) | 0.518(微改善) | 0.424(悪化) |

**3種とも、較正なしのMAEを上回れなかった。** 原因はIsotonic回帰
(ms空間)の内訳で明確に確認できる。実測が53,045msに達する裾の巨大値が
二乗誤差(isotonic回帰の内部目的関数)を支配するため、較正曲線全体が
底上げされ(decile 0の予測平均が197ms→1875ms)、大部分を占める低〜中位
の通常ケースの絶対誤差がかえって悪化する。log空間や中央値ベースの
binningで二乗誤差の支配を緩和しても、MAEは較正なしを上回れなかった。

**結論**: 「予測レンジの圧縮」は、モデル出力への事後的な単調写像では
解消できない。圧縮の原因は出力側の後処理ではなく、**decision時点の
特徴量だけでは高tail caseを他の中位caseと十分に区別できていないこと**
(情報不足)にある可能性が高い。これはitem 15の結論(残存誤差の主因は
未来の到着という原理的に予測不能な情報)とも整合する。次に試すべきは
出力の後処理ではなく、B系(候補GPU到着率などの特徴量追加)である。

結果は`analysis/ttft_formula/route_tail_isotonic_calibration_summary.csv`,
`analysis/ttft_formula/route_tail_isotonic_calibration_decile_*.csv`に保存。

## 特徴量追加(idea B)の検証

事後較正が効かなかったため、モデル出力の後処理ではなく、既に決定時点で
取得可能だが現行モデルが使っていない特徴量の追加を検証した。
`scripts/tune_feature_additions.py`を新規作成し、compute/routeそれぞれ
独立に、既存のscenario-held-out CVで検証した。

### Compute: `router_initial_running_reqs`/`waiting_reqs`を追加

現行`compute_ms`は`input_tokens`と`home_cached_prefix_tokens`の2項のみ
(`fit_ttft_formula.py::COMPUTE_FEATURES`)で、GPUの混雑度を一切見ない
設計だった(item 12/13で既知)。既にroute/scheduler側では使われている
`router_initial_running_reqs`/`waiting_reqs`をcomputeにも追加したところ、

| バリアント | compute MAE | compute R² |
|---|---:|---:|
| 現行(input_tokens, home_cached_prefix_tokens) | 59.68 ms | 0.830 |
| + running_reqs | 57.93 ms | 0.843 |
| + waiting_reqs | 57.64 ms | 0.836 |
| **+ 両方** | **56.02 ms** | **0.848** |

### Route tail: hinge特徴量 vs 交互作用特徴量

`POST_ROUTING_ANALYSIS_PLAN.md`の6.1/6.2節で提案されていた
hinge特徴量(`capacity_overload = max(0, capacity_pressure - 1)`、
`slot_overload = max(0, slot_pressure - 1)`)と、交互作用特徴量
(`home_workload_share × request_rate_rps`、
`capacity_pressure × request_rate_rps`)を、チューニング済み
hyperparameter(learning_rate=0.15, n_estimators=70)の上に追加して比較した。

| バリアント | route MAE | route R² |
|---|---:|---:|
| 現行(チューニング済みhyperparamsのみ) | 612.37 ms | 0.5114 |
| **+ hinge特徴量2個** | **602.13 ms** | **0.5122** |
| + 交互作用特徴量2個 | 631.76 ms(悪化) | 0.4636(悪化) |
| + 全4個 | 617.39 ms(hinge単独より悪化) | 0.4701 |

hinge特徴量は明確に効くが、交互作用特徴量は単独でもhingeと組み合わせても
悪化させる。深さ2の木は分岐を3個しか持てないため、`max(0, x-1)`という
閾値をあらかじめ計算しておくと分岐を節約できる一方、生の積(`a×b`)は
分布が歪みやすく標準化後の分割点探索にノイズを持ち込みやすいと解釈できる。

## 3つの改善を統合した最終効果

`scripts/evaluate_combined_improvements.py`を新規作成し、
(1) route_tailのhyperparameter調整(learning_rate=0.15, n_estimators=70)、
(2) hinge特徴量2個(`capacity_overload`, `slot_overload`、event/scheduler
とも共有のNUMERIC_FEATURESに追加されるため、route_event・scheduler
Ridgeにも副次的に効く)、(3) compute特徴量2個(`router_initial_running_reqs`,
`waiting_reqs`)を全て同時に適用し、`fit_ttft_formula.py`と同一の
scenario-held-out CVプロトコルで最終効果を確認した(production
artifactは未変更、読み取り専用のwhat-if評価)。

| 指標 | 現行production | 3つ統合後 | 差分 |
|---|---:|---:|---:|
| route_mae_ms | 630.19 | 602.13 | −28.06 ms(−4.5%) |
| scheduler_mae_ms | 48.41 | 47.83 | −0.57 ms(hinge特徴量の副次効果) |
| compute_mae_ms | 59.68 | 56.02 | −3.66 ms(−6.1%) |
| **ttft_mae_ms** | **714.64** | **680.56** | **−34.08 ms(−4.8%)** |
| **ttft_r2** | **0.4663** | **0.5227** | **+0.0564(+12.1%)** |

各コンポーネントのMAEは単純加算ではないため(誤差が一部相殺し得る)、
個別検証の差分の総和とは厳密には一致しないが、方向は完全に一致しており、
3つとも独立に寄与していることを確認した。production artifactへはまだ
反映していない。

結果は`analysis/ttft_formula/compute_feature_addition_sweep.csv`,
`analysis/ttft_formula/route_tail_feature_addition_sweep.csv`,
`analysis/ttft_formula/combined_improvements_summary.csv`,
`analysis/ttft_formula/combined_improvements_oof_predictions.csv`に保存。

## 再現

```bash
MPLCONFIGDIR=/tmp/llmservingsim-mpl PYTHONPYCACHEPREFIX=/tmp/llmservingsim-pycache LOKY_MAX_CPU_COUNT=8 \
  python3 experiments/2026-07-16_ttft_component_regression/scripts/tune_route_tail_model.py
```

```bash
MPLCONFIGDIR=/tmp/llmservingsim-mpl PYTHONPYCACHEPREFIX=/tmp/llmservingsim-pycache LOKY_MAX_CPU_COUNT=8 \
  python3 experiments/2026-07-16_ttft_component_regression/scripts/fit_ttft_formula.py \
  --output-dir /tmp/ttft_trial_lr015_70 --model-dir /tmp/ttft_trial_lr015_70_model \
  --figure-dir /tmp/ttft_trial_lr015_70_fig \
  --route-tail-n-estimators 70 --route-tail-learning-rate 0.15
```

結果は`analysis/ttft_formula/route_tail_hyperparameter_sweep.csv`,
`analysis/ttft_formula/route_tail_calibration_*.csv`に保存。
