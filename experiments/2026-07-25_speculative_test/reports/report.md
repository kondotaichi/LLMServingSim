# 投機的実行(KVキャッシュ移送)のためのTTFT予測は十分か

## 0. 動機

`experiments/2026-07-16_ttft_component_regression`で今日行った回帰精度
改善(route_tailのhyperparameter調整、hinge特徴量、compute特徴量追加。
TTFT MAE 714.6→680.6ms、R² 0.466→0.523)は、scenario-held-out CV
(n=13,500)という**集計指標**で検証したものである。しかし本来の用途は
「redirectするか、どのGPUへredirectするか」という**個別判断**であり、
集計指標の改善が個別判断の精度改善に一致するとは限らない。本実験では
既存のcounterfactualラベル付きデータ(`2026-07-21-add_gpu_utilization`、
n=18 redirect判断、161候補)を使い、今日の回帰改善が実際の判断精度を
改善したかを測定した。

## 1. 測定した内容と、測定できていない内容

### 測定した内容: redirect **先(候補)** の選択精度

18件のredirect判断それぞれについて、admissibleな候補GPU群(平均9候補)
の中から現行production式(item 13のlog1p scheduler版)と今日の統合改善式
のどちらがより実測TTFTの小さい候補を選べるかを比較した。

### 当初測定できていなかった内容: home GPUで待つか vs redirectするか、という判断自体

この18件はいずれも**baseline実行の時点でhome GPUがcapacity的に
admissibleでなかった**ケースである(admissibleだったら、そもそも
redirectせずhomeで処理されている)。したがって候補集合には「home で
待つ」という選択肢が存在せず、counterfactualの実測データも無かった。
投機的実行が本来必要とする「home vs candidate」のTTFT比較そのものの
精度は、当初この実験では検証できていなかった。**3節で、新規
counterfactualモードを実装し18件全て実測した結果を述べる。**

## 2. Redirect先選択精度: production式と統合改善式の比較(n=18)

`scripts/evaluate_combined_formula_on_counterfactuals.py`を新規作成。
統合改善式(route_tail: learning_rate=0.15/n_estimators=70、
capacity_overload/slot_overload、compute特徴量2個追加)を、
`2026-07-16_ttft_component_regression`のPhase 1データ(13,500行、
counterfactualデータとは完全に別データセット、リーク無し)で最終学習し、
161候補の`feature_*`列に適用した。

| 選択方法 | Top-1正解率 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| production(log1p scheduler、item 13) | 44.4% | 42.27 ms | 1.49 ms | 216.95 ms | 0.314 |
| **統合改善式(今日の変更)** | 44.4%(同じ) | **41.62 ms** | **0.77 ms** | 216.95 ms(同じ) | **0.419** |
| pressureヒューリスティック | 38.9% | 44.88 ms | 0.83 ms | 219.85 ms | 0.447 |

平均regret・中央値regret・Spearman順位相関は改善したが、Top-1正解率と
p95 regretは全く変わらなかった。

### 内訳: 18件中16件は選択が変わらず、改善は実質1件に集中

| request | production選択 | 統合改善式選択 | regret変化 |
|---|---|---|---:|
| 84 | GPU3 | GPU5 | +1.42 ms(悪化) |
| **161** | GPU9 | **GPU2** | **−13.17 ms(改善)** |
| 残り16件 | 同じGPU | 同じGPU | 0 |

p95を決めているrequest 51(regret 372.5ms、変化なし)は両モデルとも
GPU8を選んでおり、統合改善式でも解決していない。同点率(tie fraction)
も0.112で完全に同一だった。

### 解釈

集計指標(TTFT MAE −4.8%、R² +12.1%)は確かに実測ベースの判断精度へも
**方向として**波及しており、改悪ではなかった。しかしその効果は
`MODEL_ITERATION_HISTORY.md`のitem 10/14でも繰り返し見られたのと同じ
パターンで、**n=18のうち実質1件の判断が変わったことにほぼ依存する**
小さな改善である。p95の大外れ(request 51、372.5ms)は解決していない。
これはitem 15で確定した「決定時点では原理的に予測不能な未来到着」に
起因する誤差であり、回帰精度をどれだけ上げても解決しない種類の誤差
だという結論と整合する。

## 3. Home-vs-redirectの実測: 新しいcounterfactualモードを実装し18件全て実行

### 3.1 実装した仕組み

`router.py`に`counterfactual_force_local_request_id`を新規追加した
(`--counterfactual-force-local-request-id` CLI引数、
`counterfactual_request_id`/`counterfactual_target_instance_id`とは
併用不可)。`_maybe_capacity_dynamic_formula_route()`内で、home GPUの
admissibility判定(`_has_capacity(home, req_data)`)が false だった直後、
候補スコアリング/redirect判断に入る前に分岐を追加し、対象requestなら
候補を一切見ずに`_defer_capacity_retry(req_data, current_time_ns, home, home)`
で待たせる。これはpolicy A(`NEAREST_KV`、redirectしない)がやっている
ことと同じ挙動を、その1件のrequestにのみ強制するもので、それ以外の
状態遷移はbaselineと完全に同一である(既存の143件の候補counterfactual
と同じ「1判断だけ介入」という設計を踏襲)。

18件それぞれについて1回のシミュレーションで足りる(候補ごとに複数arm
必要だった143件の候補sweepと違い、「homeで待つ」は1通りしかないため)。
Dockerコンテナ`llmservingsim_sim_local`(astra-simはLinux x86-64向けに
ビルド済みのため、macOSホストでは直接実行不可、Docker必須)で
`experiments/2026-07-25_speculative_test/run_force_local_counterfactual.sh`
を実行し、8並列実行時に3件(request 161, 165, 197)が
`ASTRA-Sim stdout closed before a Waiting event`という偶発的な
リソース競合エラーで失敗、3並列で再実行して18件全て完了した
(`analysis/`配下の`status/*.status`が全て`COMPLETED`、各`requests.csv`が
301行であることを確認済み)。

### 3.2 結果: redirectは83.3%で正しかったが、判断根拠は「予測」ではなく「安全マージンの飽和」

`scripts/analyze_home_vs_redirect.py`を新規作成し、各requestについて
実測`home_actual_ttft_ms`(force-local実行)、実測
`actual_redirect_ttft_ms`(baseline実行)、送信時点の
`oneshot_predicted_local_ttft_ns`(baselineのrequests.csvに既に記録済み)
を突き合わせた。

| 指標 | 値 |
|---|---:|
| redirectが正しい判断だった件数(home待機より速いか同等) | **15/18(83.3%)** |
| redirectがむしろ悪化させた件数 | 3/18(16.7%: request 67, 156, 205) |
| redirect benefit(home実測−redirect実測)平均 | +1,789.95 ms |
| redirect benefit 中央値 | +1,081.86 ms |
| 悪化した場合の最大損失 | 276.93 ms(request 67) |
| **local予測誤差(予測local−実測home)平均** | **+15,041.82 ms** |
| **local予測誤差の最小値(18件中最も当たっていたケースでも)** | **+10,427.98 ms** |

18件全ての`oneshot_decision_reason`が`predicted_local_wait_exceeds_limit`
で揃っており、`local_total`(=`route_upper_ms`ベースの上側予測、
item 13の90%ile安全マージンを含む)は実測homeより常に1万〜1.9万ms
大きい。この安全マージンは`oneshot_max_local_wait_ns`(1秒)より
常に大きいため、home GPUの実際の空き具合に関わらず**ほぼ機械的に
「local予測は上限超え→redirect」という判定になっている**ことが、
今回初めて実測データで定量的に確認された。これは2026-07-22の日誌
(`kondoFolder/diary/2026-07-22-seminor-report.md`)で「懸念」として
記述されていた「local待機ゲートの飽和」を裏付ける初の実測結果である。

### 3.3 解釈: 83.3%という数字は「賢い判断」の証拠ではない

上記の飽和構造を踏まえると、83.3%という正解率は「モデルが状況を見て
判断した結果」ではなく、「このworkloadでは大抵redirectした方が得
だった」という**環境側の性質**をたまたま反映しているに過ぎない。
悪化した3件(request 67, 156, 205)は実測home待機がそれぞれ
581.7 / 716.4 / 480.2 msと短く、「もう少し待てば良かった」ケースだが、
安全マージンが常に飽和しているため区別できていない。

**投機的実行への含意**: 現行の`local_total`はrouting判断の閾値判定用に
意図的に膨らませた値であり、そのままKVキャッシュ投機的移送の
トリガーには使えない。素の点予測(`route_ms`、安全マージン抜き)を
home側にも使うか、home側の予測にも今日の回帰改善(item 16、
`experiments/2026-07-16_ttft_component_regression`)を適用した上で
再較正する必要がある。この再較正・再評価は未実施であり、次の
アクション候補である。

## 4. 現状のまとめ

| 問い | 状況 |
|---|---|
| Redirect**先**の選択精度は今日の回帰改善で悪化したか | していない(2節、n=18のうち1件の改善に依存する小さな前進) |
| Redirectという**判断自体**は実測上正しかったか | 83.3%(15/18)で正しかったが、環境依存であり、モデルの判断力の証拠ではない |
| Local待機の予測(投機的実行のトリガーに使う値)は信頼できるか | **信頼できない**。安全マージンにより常に10,000〜19,000ms過大評価されており、`oneshot_decision_reason`が18件全てで同一の理由に飽和している |
| 投機的実行にそのまま使ってよいか | **まだ使えない**。home側の点予測を安全マージン抜きで再較正し、再評価する必要がある |

## 再現

```bash
MPLCONFIGDIR=/tmp/llmservingsim-mpl PYTHONPYCACHEPREFIX=/tmp/llmservingsim-pycache LOKY_MAX_CPU_COUNT=8 \
  python3 experiments/2026-07-25_speculative_test/scripts/evaluate_combined_formula_on_counterfactuals.py
```

```bash
docker start llmservingsim_sim_local
docker exec -d -e MAX_PARALLEL=3 -e SKIP_COMPLETED=1 llmservingsim_sim_local \
  bash -lc 'cd /app/LLMServingSim && bash experiments/2026-07-25_speculative_test/run_force_local_counterfactual.sh > experiments/2026-07-25_speculative_test/logs/run_all.log 2>&1'
```

```bash
python3 experiments/2026-07-25_speculative_test/scripts/analyze_home_vs_redirect.py
```

結果は`analysis/combined_formula_vs_production_summary.csv`,
`analysis/combined_formula_vs_production_by_request.csv`,
`analysis/counterfactual_candidate_actuals_combined_formula.csv`,
`analysis/home_vs_redirect_by_request.csv`,
`analysis/home_vs_redirect_summary.csv`に保存。
