# 2026-07-22 セミナー報告: TTFT定式化の整理とルーティングモデルの現状

## 0. 本日の報告内容

1. 現状作成できた定式化(`OfflineTtftFormula`)の整理
2. 定式化の回帰係数から読み取れる重要因子
3. トークンサイズ×prefix reuse率別に見た、KVキャッシュ移送方式(KV handoff)の
   メリット(compute/router queue/scheduler queue/KV transferの差分)
4. 複数候補探索(Multi-candidate)は性能を大きく改善したが、学習モデルはそこまで
   改善しなかったという結果と、その原因・学習モデルの現状の課題
5. 学習モデル改善のために進めているcounterfactualシミュレーション(選ばなかった
   候補GPUを実際に選んだ場合の実測)の状況

---

## 1. 現状作成できた定式化(`OfflineTtftFormula`)の整理

`serving/core/ttft_formula.py`が実装する、request送信時点の情報からE2E TTFTを
予測する式。学習は`experiments/2026-07-16_ttft_component_regression`で行い、
係数は`experiments/2026-07-16_ttft_component_regression/analysis/ttft_formula/`
にCSV/JSONとして保存され、simulatorはこれを直接読み込んで評価する
(依存ライブラリ無しで再実装可能)。

$$
\widehat{TTFT} = \hat t_{route} + \hat t_{sched} + \hat t_{compute} \quad (+\ \hat t_{comm}=0\text{として省略})
$$

各成分をざっくり展開すると、次の式である。

$$
\boxed{
\widehat{TTFT}
\approx
P(\text{Router queue発生})
\times
E[\text{Router待機時間}\mid\text{queue発生}]
+\text{Scheduler待機時間}
+\text{Prefill計算時間}
}
$$

記号で書けば、

$$
\boxed{
\widehat{TTFT}
=
p_{route}\hat t_{route,+}
+\hat t_{sched}
+\hat t_{compute}
}
$$

であり、$\hat t_{route}=p_{route}\hat t_{route,+}$である。通信時間はこのdatasetでは
平均影響が小さく、redirect後に確定するためpoint predictionから省略している。

| 成分 | モデル形式 | 意味 |
|---|---|---|
| $\hat t_{route}$ | ロジスティック回帰(発生確率) × 回帰木100本(発生した場合の時間、log変換+clip) | Router queue(容量待ち)時間の期待値 |
| $\hat t_{sched}$ | Ridge回帰(alpha=1000)、下限0でclip | Scheduler queue(batch投入までの待ち)時間 |
| $\hat t_{compute}$ | 単純な線形式、下限0でclip | Compute/prefill時間 |

学習データ: Phase 1 capacity-boundary実験、45 runs・15 scenarios・13,500
requests、leave-one-scenario-outで評価。

| 指標 | 値 |
|---|---:|
| Queue発生 ROC-AUC | 0.99995 |
| Queue発生 PR-AUC | 0.99971 |
| $t_{route}$ MAE | 630.2 ms |
| $t_{sched}$ MAE | 56.6 ms |
| $t_{compute}$ MAE | 59.7 ms |
| **TTFT MAE** | **716.1 ms** |
| **TTFT R²** | **0.467** |

Absolute TTFT誤差はp50 73.2ms、p90 1,015ms、p95 4,050ms、p99 13,578ms。
通常域はそこそこ当たるが、高負荷long-tail(例: `input6000_rate5p0_reuse00_seed1`
scenarioでMAE 3.71秒)を系統的に過小予測する。このため、point prediction
とは別に、routing判断(local待機かredirectか)専用に、scenario-held-out
residualの90 percentileを加えた上側予測 $\hat t_{route,upper} = \hat
t_{route,+} + 10{,}311.69\,\mathrm{ms}$ を使い分けている。

---

## 2. 回帰係数から読み取れる重要因子

### Router queue発生確率(ロジスティック回帰)

標準化係数の絶対値が大きい順。正係数は発生確率を上げる方向。

| 特徴量 | 係数 |
|---|---:|
| `router_initial_capacity_pressure` | +2.426 |
| `router_initial_projected_active_kv_bytes` | +2.397 |
| `router_initial_available_kv_bytes` | −2.397 |
| `router_initial_running_reqs` / `slot_pressure` | +1.743 |
| `input_tokens` | +1.200 |
| `router_initial_admissible_candidate_count` | −1.194 |
| `router_initial_required_kv_bytes` | +1.192 |

→ **KV容量の逼迫度(capacity pressure)が最大の支配要因**。available/projected
KV bytesとrunning/slot pressureは決定的に相関するため個別係数を独立な因果
効果とは解釈しない。

### Router queue発生時の待機時間(回帰木)

Router queueは発生確率だけでなく、**queueが発生した場合に何ms待つか**も別モデルで
定式化している。`router_queue_ms > 0`のrequestだけを使い、待機時間を`log1p`変換した
上で、深さ2の回帰木100本によるGradient Boostingを学習した。

$$
g(x)=8.324456+\sum_{m=1}^{100}0.05\,v_{m,leaf_m(x)}
$$

$$
\hat t_{route,+}
=
\exp\left(clip(g(x),0.001642,10.878924)\right)-1
$$

$\hat t_{route,+}$が「Router queueが発生したという条件の下での正値待機時間」である。
前節の発生確率$p_{route}$と掛け合わせ、point predictionのRouter成分を得る。

$$
\boxed{
\hat t_{route}
=
p_{route}\hat t_{route,+}
}
$$

したがって現行モデルは、次のzero-inflatedな二段階構成である。

1. Logistic回帰でRouter queueが発生する確率$p_{route}$を予測
2. 回帰木で発生時の待機時間$\hat t_{route,+}$を予測
3. 両者の積を期待Router待ち時間$\hat t_{route}$とする

正値待機時間モデルで重要だったのは、`router_initial_admissible_candidate_count`、
`policy_NEAREST_KV`、`home_workload_share`、`router_initial_min_capacity_pressure`、
`arrival_offset_s`などである。Queue発生後に「いつ収容可能なGPUが現れるか」を、
逃げ先の数、routing policy、局所負荷の持続性から推定している。

ただし、この正値tailのscenario-held-out外挿精度が不十分で、長い待機を過小予測する。
そのためroutingの安全判定では、point predictionとは別に次の上側予測を使う。

$$
\hat t_{route,upper}
=
\hat t_{route,+}+10{,}311.69\,\mathrm{ms}
$$

この10.3秒は待機時間モデル自体の出力ではなく、scenario-held-out residualの
90 percentileを一律に加えた安全補正である。この補正が1秒のlocal待機上限より
常に大きいため、現在のMulti学習モデルではlocal待機ゲートが飽和する原因になっている。

### Scheduler queue時間(Ridge回帰)

| 特徴量 | 係数(ms/1SD) |
|---|---:|
| `router_initial_waiting_reqs` | +52.73 |
| `home_arrivals_1s` | +50.73 |
| `home_cached_prefix_tokens` | −17.52 |
| `home_workload_share` | −8.11 |
| `home_arrivals_5s` | +7.80 |
| `router_initial_required_kv_bytes` | +7.63 |
| `input_tokens` | +7.51 |

→ **Home GPUの直近到着率(`home_arrivals_1s`)と待機列の長さ(`waiting_reqs`)
が支配的**。cache reuse(`home_cached_prefix_tokens`)は待ち時間を減らす方向
に効く。

### Compute/prefill時間(線形回帰)

$$
\hat t_{compute} = \max(0,\ -96.72 + 0.14402\cdot inputTokens - 0.11901\cdot homeCachedPrefixTokens)
$$

係数はこの2項のみ。**候補GPUの混雑度・同時batch数・待ち仕事量を表す変数が
一切含まれていない**(詳細は4節・別途の議論を参照)。同じ実験内の
post-routing支配要因分析(`2026-07-16_ttft_component_regression/reports/
post_routing_component_dominance_v2.md`)でも、schedule時のbatch状態を
特徴量に加えるとcompute MAEが平均10.88ms改善することが既に確認されており、
「computeはuncached token量だけでなく同一batchのsequence数・decode/prefill
構成にも依存する」ことは分かっていたが、この情報はrouting判断の時点では
未来の情報(post-schedule)であるため、現行のPost-routing式には反映されて
いない。

### LightGBM特徴量重要度から見た支配要因

線形係数だけでは捉えにくい非線形性と特徴量間相互作用を確認するため、同じ13,500
requests・15 scenariosに対し、Router queue発生、正値Router queue時間、Scheduler queue、
Compute/prefillの4 componentをLightGBMでも学習した。評価分割は既存formulaと同じ
leave-one-scenario-outである。

![LightGBM feature importance](../../experiments/2026-07-16_ttft_component_regression/lightgbm/figures/feature_importance_gain.png)

各componentのtotal gain上位は次の通りだった。

| Component | 主な特徴量 | Gain fraction | 解釈 |
|---|---|---:|---|
| Router queue発生 | `router_initial_capacity_pressure` | 81.3% | 新規requestを含めたKV収容圧力がqueue発生をほぼ決める |
| Router queue発生 | `projected_active_kv_bytes` | 14.1% | 既存request群が将来必要とするKV量 |
| 正値Router時間 | `policy_NEAREST_KV` | 19.2% | Homeで待つか、別GPUへ逃げられるか |
| 正値Router時間 | `admissible_candidate_count` | 17.0% | 即時に利用できる逃げ先GPU数 |
| 正値Router時間 | `home_workload_share` | 10.4% | Home側への継続的な負荷集中 |
| Scheduler | `router_initial_running_reqs` | 32.6% | 既存batch内のrequest数とsequence/token budget競合 |
| Scheduler | `capacity_pressure` | 16.4% | KV解放待ちを含む収容余力 |
| Scheduler | `home_arrivals_1s` | 12.8% | 対象GPUへの短時間arrival burst |
| Compute | `input_tokens` | 33.9% | Prefillで処理する元のprompt量 |
| Compute | `home_cached_prefix_tokens` | 26.1% | 再計算を省略できるprefix量 |

この結果から、TTFTの支配構造は負荷regimeによって次のように切り替わると解釈できる。

1. **低負荷・通常時**: Router queueがほぼ0なので、`input_tokens`と
   `home_cached_prefix_tokens`が決めるCompute/prefillが支配的。
2. **Capacity境界付近**: 新規requestをGPUへ収容できるかどうかが重要になり、
   KV capacity pressureがRouter queue発生を支配する。
3. **過負荷・long-tail**: Queue発生後にいつ収容可能なGPUが現れるかが重要になり、
   routing policy、admissible GPU数、homeへの負荷集中が支配的。
4. **Scheduler待ち**: 対象GPUのrunning request数と直近1秒のarrival burstが支配的。

したがって、全regimeをまとめた結論は、**通常時のTTFTは「何tokenをprefill計算するか」、
tail TTFTは「GPUへ収容できるか、別GPUへ逃げられるか」で決まる**、となる。

一方、LightGBMのscenario-held-out結果は、Router MAEが630.2→612.3ms、Scheduler MAEが
56.6→39.4msへ改善したが、Compute MAEは59.7→69.7msへ悪化し、最終TTFT MAEも
716.1→706.8msの小幅改善に留まった。Computeについて追加のGPU状態特徴にもgainが
付いたものの、未知scenarioへは安定して一般化せず、既存のtoken数による単純線形式の方が
良かった可能性がある。

なお、LightGBMのgain importanceは因果効果ではない。`capacity_pressure`、
`projected_active_kv_bytes`、`available_kv_bytes`は同じKV状態から派生する強相関特徴であり、
gainがいずれか1列へ集中する。個別列の順位ではなく、**KV収容余力**、**request sizeと
prefix reuse**、**既存backlogとarrival burst**、**逃げ先の有無**という特徴量群として
解釈する。

---

## 3. トークンサイズ × reuse率別に見たKVキャッシュ移送(KV handoff)のメリット

社内の全実験(`2026-07-14_input_reuse_90s_sweep`、
`2026-07-16_ttft_component_regression/phase1`、
`2026-07-16_input10000_reuse05_180s_three_policy_良結果`、
`2026-07-21-add_gpu_utilization`)から、`NEAREST_MIGRATE`(KV移送なし)と
`NEAREST_MIGRATE_KV`(KV移送あり)の両方が存在する条件を全て洗い出し、
input 512/2000/4000/6000/8000/10000トークン、reuse 0/25/50%の組み合わせで
比較した。図は全て
`kondoFolder/diary/2026-07-22-seminor-report/*_ttft_breakdown.png`
に出力済み(`generate_kv_breakdown_figures.py`で再生成可能)。

### 全条件のTotal mean E2E TTFT(全リクエスト平均、ms)

同一条件(同一workload jsonl・同一クラスタ設定・同一到着時間窓)でMulti-candidate
ポリシーの実行が存在するものは、`Multi(no-model)`(capacity pressureヒューリス
ティック、`NEAREST_CAPACITY_MULTI_PRESSURE(_FORMULA)_KV_RESERVE`)/
`Multi(学習モデル)`(`NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`系)の列を追記した
(図は全て`generate_kv_breakdown_figures.py`を再実行して4〜5手法版に更新済み)。
同一条件のMulti実行が存在しないもの(input4000、input6000/8000のphase1系列)は
空欄のまま。

各条件の内訳図(breakdown)に加えて、同じ手法セットのE2E TTFT CDF図
(`generate_kv_cdf_figures.py`、全件/redirectのみ/非redirectのみの3パネル、
横軸logスケール、p50/p90/p95/p99の目安線付き)も生成した。「差分(KV−なし)」列
がmeanで見た改善幅、CDF図はp50〜p99のどこで差が付いているか(裾だけ改善して
いるのか全体がシフトしているのか)を切り分けるために使う。

| 条件 | Migrate(KVなし) | Migrate+KV(KVあり) | 差分(KV−なし) | Multi(no-model) | Multi(学習モデル) | CDF |
|---|---:|---:|---:|---:|---:|---|
| [Prompt 6000 / 90s (ShareGPT, reuse~50%)](2026-07-22-seminor-report/prompt6000_90s_ttft_breakdown.png) | 518.5 | 487.1 | **−31.5** | 470.5 | 471.7 | [図](2026-07-22-seminor-report/prompt6000_90s_ttft_cdf.png) |
| [Input 512 / reuse 0%](2026-07-22-seminor-report/input512_reuse00_ttft_breakdown.png) | 65.9 | 65.9 | 0(redirect無し) | 65.9 | — | [図](2026-07-22-seminor-report/input512_reuse00_ttft_cdf.png) |
| [Input 512 / reuse 25%](2026-07-22-seminor-report/input512_reuse025_ttft_breakdown.png) | 53.5 | 53.5 | 0(redirect無し) | 53.5 | — | [図](2026-07-22-seminor-report/input512_reuse025_ttft_cdf.png) |
| [Input 512 / reuse 50%](2026-07-22-seminor-report/input512_reuse05_ttft_breakdown.png) | 40.7 | 40.7 | 0(redirect無し) | 40.7 | — | [図](2026-07-22-seminor-report/input512_reuse05_ttft_cdf.png) |
| [Input 2000 / reuse 0%](2026-07-22-seminor-report/input2000_reuse00_ttft_breakdown.png) | 233.9 | 233.9 | 0(redirect無し) | 233.9 | — | [図](2026-07-22-seminor-report/input2000_reuse00_ttft_cdf.png) |
| [Input 2000 / reuse 25%](2026-07-22-seminor-report/input2000_reuse025_ttft_breakdown.png) | 180.2 | 180.2 | 0(redirect無し) | 180.2 | — | [図](2026-07-22-seminor-report/input2000_reuse025_ttft_cdf.png) |
| [Input 2000 / reuse 50%](2026-07-22-seminor-report/input2000_reuse05_ttft_breakdown.png) | 124.0 | 124.0 | 0(redirect無し) | 124.0 | — | [図](2026-07-22-seminor-report/input2000_reuse05_ttft_cdf.png) |
| [Input 4000 / rate2.5 / reuse 0%](2026-07-22-seminor-report/input4000_rate2p5_reuse00_ttft_breakdown.png) | 503.8 | 503.8 | 0(redirect無し) | — | — | [図](2026-07-22-seminor-report/input4000_rate2p5_reuse00_ttft_cdf.png) |
| [Input 6000 / rate3.33 / reuse 0%](2026-07-22-seminor-report/input6000_rate3p33_reuse00_ttft_breakdown.png) | 1241.0 | 1241.0 | 0(redirect無し) | — | — | [図](2026-07-22-seminor-report/input6000_rate3p33_reuse00_ttft_cdf.png) |
| [Input 6000 / rate3.33 / reuse 50%](2026-07-22-seminor-report/input6000_rate3p33_reuse50_ttft_breakdown.png) | 594.4 | 596.5 | **+2.1(ほぼ無/わずかに悪化)** | — | — | [図](2026-07-22-seminor-report/input6000_rate3p33_reuse50_ttft_cdf.png) |
| [Input 8000 / rate2.5 / reuse 0%](2026-07-22-seminor-report/input8000_rate2p5_reuse00_ttft_breakdown.png) | 2261.8 | 2261.8 | 0(redirect無し) | — | — | [図](2026-07-22-seminor-report/input8000_rate2p5_reuse00_ttft_cdf.png) |
| [Input 8000 / rate2.5 / reuse 50%](2026-07-22-seminor-report/input8000_rate2p5_reuse50_ttft_breakdown.png) | 1007.1 | 867.8 | **−139.3** | — | — | [図](2026-07-22-seminor-report/input8000_rate2p5_reuse50_ttft_cdf.png) |
| [Input 10000 / reuse 0% / 90s](2026-07-22-seminor-report/input10000_reuse00_ttft_breakdown.png) | 18036.4 | 18036.4 | 0(reuseなし) | 14773.9 | — | [図](2026-07-22-seminor-report/input10000_reuse00_ttft_cdf.png) |
| [Input 10000 / reuse 25% / 90s](2026-07-22-seminor-report/input10000_reuse025_ttft_breakdown.png) | 15574.2 | 16162.0 | **+587.8(悪化)** | 12012.2 | — | [図](2026-07-22-seminor-report/input10000_reuse025_ttft_cdf.png) |
| [Input 10000 / reuse 50% / 90s](2026-07-22-seminor-report/input10000_reuse05_ttft_breakdown.png) | 12913.5 | 10835.0 | **−2078.4(大幅改善)** | 8614.9 | — | [図](2026-07-22-seminor-report/input10000_reuse05_ttft_cdf.png) |
| [Input 10000 / reuse 50% / 180s(低負荷版)](2026-07-22-seminor-report/input10000_reuse05_180s_ttft_breakdown.png) | 967.5 | 874.4 | **−93.2** | — | 820.5 | [図](2026-07-22-seminor-report/input10000_reuse05_180s_ttft_cdf.png) |

(生データ: [`all_conditions_ttft_breakdown.csv`](2026-07-22-seminor-report/all_conditions_ttft_breakdown.csv)、
CDFの分位点生データ: [`all_conditions_ttft_cdf_quantiles.csv`](2026-07-22-seminor-report/all_conditions_ttft_cdf_quantiles.csv)。
redirect済みのみ/未redirectのみへの分解は各PNGの中央・右パネル参照。
Multi(no-model)の出所: prompt6000_90sは`2026-07-21-add_gpu_utilization`(five-policy実験、4節と同一実行)、
input512/2000/10000×reuse00/025/05は`2026-07-21_multi_pressure_vs_kv_input_reuse_sweep`
(`2026-07-14_input_reuse_90s_sweep`と同一workload jsonlを再利用した検証実験)。
Multi(学習モデル)の出所: prompt6000_90sは同じくfive-policy実験、
input10000/reuse50%/180sは`2026-07-16_input10000_reuse05_180s_three_policy_良結果`。)

KV移送(Migrate+KV)とMulti-candidateを並べると、input10000/90s系列では
Multi(no-model)がMigrate+KVをさらに大きく上回る(reuse50%: 10835.0→8614.9ms、
−20.5%)。これは4節で述べた「Multi-candidateはredirect先を全GPUに分散でき、
KV移送単体よりrouter queueを削れる」ことの裏付けであり、KVキャッシュ移送の
効果とMulti-candidate探索の効果は独立に積み上がる別レバーであることが
この表からも読み取れる。

### Input 10000のredirect対象のみに絞った内訳(既報の再掲)

| Reuse率 | Router queue B→C | Compute B→C | KV transfer(C) | Mean E2E B→C |
|---|---|---|---|---|
| 0% | 33.00s→33.00s | 1355→1355ms | 0ms | 34.42s→34.42s(reuseなしのため差なし) |
| 25% | 29.10s→32.56s(+3.46s) | 1367→1099ms(**−268ms**) | 264ms | 30.56s→34.03s(+3.47s、KV移送コストと経路変化で悪化) |
| 50% | 24.66s→18.87s(**−5.79s**) | 1369→793ms(**−577ms**) | 528ms | 26.12s→20.31s(**−5.82s**、大幅改善) |

### 読み取れること: KV移送の旨みが大きくなる条件

1. **入力トークン数が小さいと、そもそも比較にならない**: 512・2000・
   4000・6000/8000のreuse0%条件では、このクラスタ規模と到着率では
   redirect自体が1件も発生せず、KV移送の効果を測る土俵にすら乗らない。
   KV移送が意味を持つのは、まず「redirectが実際に起きる」過負荷寄りの
   条件に限られる。
2. **reuse率が低いと、移送コストが利益を相殺し、むしろ悪化する**:
   input6000/rate3.33のreuse50%では+2.1ms(ほぼ無)、input10000/90sの
   reuse25%では+587.8ms悪化。KV移送によって到着・完了のタイミングが変わり、
   後続のcapacity判定・redirect対象集合がずれることで、router queueの
   trajectory自体が変化し、raw computeの節約分だけでは説明できない悪化が
   起きる。
3. **「入力トークン数」と「reuse率」が両方大きい条件でのみ、旨みが
   大きくなる**: 同じreuse50%でも、token数が大きいほど恩恵が拡大する。

   | Token数 | reuse率 | KV移送の効果(全リクエスト平均) |
   |---:|---:|---:|
   | 6000 | 50% | +2.1 ms(ほぼ無/わずかに悪化) |
   | 8000 | 50% | −139.3 ms(小さいが改善) |
   | 10000 | 50% | **−2078.4 ms(大幅改善)** |

   これは「reuseで節約できる絶対トークン数」がtoken数に比例して増えることに
   加え、token数が大きいほどprefillそのものが長時間化してGPUのKV占有時間が
   延び、KVの早期解放が後続requestのrouter queueに効くレバレッジが大きく
   なるためと解釈できる。
4. **旨みの正体は「computeの短縮」そのものではなく「router queueの連鎖的
   短縮」である**: input10000/reuse50%では、redirect対象のcompute短縮
   (約577ms)はKV transferコスト(528ms)とほぼ相殺してしまう。E2E TTFT
   改善の主因は、KVの早期解放によって**router queueが全体で2.07秒短縮**
   したことであり、これは「移送された当該requestが速くなった」効果では
   なく「他の後続requestの容量待ちが減った」間接効果である。
5. **負荷レジーム自体も独立に効く**: 同じinput10000/reuse50%でも、
   90s版(到着率が高く、そもそものrouter queueが12.9秒と大きい)では
   −2078msの改善だが、180s版(到着率が低く、router queueがそもそも
   小さい低負荷レジーム)では−93msに留まる。**KV移送で削れるのは
   「そもそも存在するrouter queueの大きさ」に比例するため、土台となる
   混雑度が低いと絶対的な旨みも小さくなる**。
6. まとめると、KV移送の旨みは **(a) redirectが実際に起きるだけの
   token数・到着率であること、(b) reuse率がある閾値(このworkload群では
   概ね50%前後)を超えていること、(c) token数が大きいほど同じreuse率でも
   効果が拡大すること、(d) 土台の混雑度(router queueの絶対量)が大きい
   ほど絶対的な改善幅も大きくなること** の組み合わせで決まる。どれか1つが
   欠けると、旨みはゼロ・無視できる水準・あるいはマイナスになる。

---

## 4. Multi-candidate探索は効いたが、学習モデルはそこまで効かなかった

### 4.1 結果

**five-policy実験(`2026-07-21-add_gpu_utilization`)、300 req/90s、10 GPU:**

| Policy | Mean TTFT | p95 TTFT | Redirects |
|---|---:|---:|---:|
| A: Nearest only(redirectなし) | 2004.1 ms | 14021.0 ms | 0 |
| B: Redirect / cold(KV移送なし) | 518.5 ms | 917.9 ms | 24 |
| C: Redirect / KV(KV移送あり) | 487.1 ms | 785.6 ms | 24 |
| D: Multi無model(capacity pressureヒューリスティック) | 470.5 ms | 758.6 ms | 18 |
| E: Multi学習モデル | 471.7 ms | 747.8 ms | 18 |

**Input×reuseスイープ(`2026-07-21_multi_pressure_vs_kv_input_reuse_sweep`)、
Multi無model vs KV migrate単体:**

512/2000トークンでは完全に無差別(redirect 0件)。**10000トークンでのみ
mean改善18.1〜25.7%、p99改善61.7〜75.9%**(p50はむしろ21〜53%悪化=一部の
極端なテール改善が平均を押し下げる形)。メカニズムは、KV migrateがredirect
先を1〜2 GPUに集中させる(例: 93件中34件が1台に集中)のに対し、Multi無model
は231件のredirectを10 GPUにほぼ均等分散させ、router queueを大幅に削減する
こと。

**混合ワークロード実験(`2026-07-21_mixed_workload_model_value`)、
入力長・reuse率・トラフィック相を1実行内で混在:**

3.33rpsで、no-model Multiは KV migrateに対しmean 15.2%・p95 19.3%・
p99 42.8%改善。一方、**学習モデルMultiはno-model Multiよりmeanで9.5ms
遅く**、p50/p95/p99でも劣る。seed一貫性を見ても、3.33rpsの全seedで
学習モデルが一貫して悪化(+2.23/+14.70/+11.49ms)。入力長別でもほぼ全ての
長さで学習モデルが悪化。学習モデルによるredirectの予測誤差はMAE 127.3ms、
p95絶対誤差497.8ms。

### 4.2 なぜMulti-candidate(探索範囲の拡大)は効いたか

Home GPUが詰まった時に「2番目に近いGPU」だけでなく**クラスタ全体の
admissibleな候補**を見て空いている所へ送るだけで、redirect先が1〜2台に
集中する現象を解消し、router queueのテールを大きく削減できる。これは
学習モデルなしの単純なヒューリスティック(capacity pressure最小)だけで
達成できている。

### 4.3 なぜ学習モデルはそこまで効かなかったか / 現状の課題

1. **Local待機ゲートが飽和している**: 学習モデルによるredirectは全て
   `predicted_local_wait_exceeds_limit`が理由。Home GPUがブロックされた
   時点で上側予測が常に1秒の限度を超えてしまい、待つかredirectするかの
   繊細な判断ができず、候補の順位付けにしか効いていない。
2. **候補間の予測が実質的に区別できていないケースがある**: counterfactual
   検証中のrequest 90では、9候補中8候補の`predicted_total_ttft_ms`が
   小数点以下まで完全に同一の値になっていた。
3. **compute_msが候補の混雑度を一切考慮しない**(2節・別議論で詳述): 同じ
   入力なら、着地GPUがどれだけ混んでいても予測されるcompute時間は変わら
   ない。しかし実際のシミュレータでは、`prefill_service_ns`はそのバッチ
   全体の所要時間を積算しており(同時に乗る他リクエストのtotal_len・
   decode数に依存)、混雑度は実際のcompute時間の一次的な決定要因である。
4. **候補順位が単純ヒューリスティックと39%しか一致しない**(18 redirectの
   診断)。
5. **Counterfactual実測での順位精度**: n=4〜6の小サンプルながら、学習
   モデルのTop-1正解率は一貫して0%(pressureヒューリスティックは
   20〜33%)、Spearman順位相関もほぼ0(pressureは0.40〜0.49)。

---

## 5. 学習モデル改善のためのcounterfactualシミュレーション

学習モデルの実測ベースの精度を測るには、「モデルが選ばなかった候補GPUに
実際に送っていたら何秒だったか」という正解データが必要だが、ベースライン
実行では選ばれた1候補にしか実際にリクエストを流していないため、この正解
データが存在しない。

そこで`router.py`に`--counterfactual-request-id` /
`--counterfactual-target-instance-id`を追加し、「特定の1回のredirect判断
だけを指定候補へ強制し、それ以外はベースライン通りに進める」one-decision
counterfactual再実行の仕組みを実装した。18回のredirect判断・161候補
(ベースラインで既知の18件を除く143件が要シミュレーション)を対象に、
現在も実行を継続中(2026-07-22時点で6/18リクエストがフル解決、143件中
約50件が完了)。

これにより`scripts/evaluate_candidate_models.py`でleave-one-request-out
評価ができるようになり、候補固有特徴量を追加した二段階(残差)モデルの
検証を開始した。ただしn=5→n=6の間だけでも結果が大きく反転する
(候補固有特徴量ありのモデルのregretが8.1msから68.9msへ悪化するなど)
ことを確認しており、**現時点ではどのモデル案が優れているかを結論づけず、
counterfactualデータを増やしながら継続監視する方針**としている。
