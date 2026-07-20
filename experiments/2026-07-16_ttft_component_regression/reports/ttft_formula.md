# Request送信時情報によるTTFT定式化

## 最終式

Request送信時に既知のrequest/context、home GPU cache情報、initial GPU telemetryから
TTFTを次式で予測する。時間の単位はすべてmsである。

$$
\boxed{
\widehat{TTFT}
=
\hat t_{route}
+\hat t_{sched}
+\hat t_{compute}
+\hat t_{comm}
}
$$

本datasetではsend後のredirectで通信時間が決まるため、point predictionは
$\hat t_{comm}=0$とした。この省略によるOOF MAEは2.88 msで、全体誤差への影響は小さい。

## 入力の標準化

Logistic、route-tail、scheduler式の数値入力は次のz-scoreへ変換する。

$$
z_j=\frac{x_j-\mu_j}{\sigma_j}
$$

$\mu_j,\sigma_j$は`analysis/ttft_formula/numeric_feature_scaling.csv`に全27列を保存した。
policyはone-hot encodingする。学習時に存在したcategoryは`NEAREST_KV`、
`NEAREST_MIGRATE`、`NEAREST_MIGRATE_KV`である。

## Router queue発生確率

$$
p_{route}=\sigma\left(\beta_0+\sum_j\beta_j z_j+
\sum_k\beta^{policy}_k I(policy=k)\right)
$$

ここで$\sigma(a)=1/(1+e^{-a})$、interceptは$\beta_0=-14.013232$である。
絶対値の大きい係数は次の通りである。

| Input | Mean | Scale | Logistic coefficient |
|---|---:|---:|---:|
| `router_initial_capacity_pressure` | 0.60498 | 0.31546 | +2.42623 |
| `router_initial_projected_active_kv_bytes` | CSV参照 | CSV参照 | +2.39727 |
| `router_initial_available_kv_bytes` | CSV参照 | CSV参照 | −2.39727 |
| `router_initial_running_reqs` | 5.403 | 3.226 | +1.74253 |
| `router_initial_slot_pressure` | CSV参照 | CSV参照 | +1.74253 |
| `input_tokens` | 6,300 | 1,307.67 | +1.20034 |
| `router_initial_admissible_candidate_count` | 8.360 | 2.244 | −1.19363 |
| `router_initial_required_kv_bytes` | CSV参照 | CSV参照 | +1.19248 |

正係数はqueue発生確率を上げ、負係数は下げる。ただしavailable/projected KV、
running/slot pressureは決定的に相関するため、個別係数を独立な因果効果とは解釈しない。
全係数は`route_event_logistic_coefficients.csv`に保存した。

## 正値Router queue時間

単純なlog-Ridge係数式はscenario-held-outで外挿に失敗したため、深さ2の回帰木100本による
piecewise係数式を採用した。

$$
g(x)=8.324456+\sum_{m=1}^{100}0.05\,v_{m,leaf_m(x)}
$$

$$
\hat t_{route,+}
=
\exp\left(clip(g(x),0.001642,10.878924)\right)-1
$$

各木は最大3個の分岐条件を持ち、$v_{m,leaf}$がそのleaf係数である。全treeの入力列、
標準化threshold、left/right node、leaf係数を
`route_positive_tree_coefficients.json`に保存した。これはblack-box model artifactだけでなく、
上式をJSONからそのまま再実装できる完全な係数表である。

ゼロ過多を考慮したrouter componentは期待値として次式で合成する。

$$
\boxed{\hat t_{route}=p_{route}\hat t_{route,+}}
$$

## Scheduler queue時間

$$
\hat t_{sched}
=
\max\left(0,\gamma_0+\sum_j\gamma_jz_j+
\sum_k\gamma^{policy}_kI(policy=k)\right)
$$

Ridge penaltyは`alpha=1000`、interceptは$\gamma_0=60.427288$ msである。
絶対値の大きい係数は次の通りである。

| Input | Coefficient (ms per 1 SD) |
|---|---:|
| `router_initial_waiting_reqs` | +52.7343 |
| `home_arrivals_1s` | +50.7336 |
| `home_cached_prefix_tokens` | −17.5240 |
| `home_workload_share` | −8.1111 |
| `home_arrivals_5s` | +7.8022 |
| `router_initial_required_kv_bytes` | +7.6343 |
| `input_tokens` | +7.5137 |
| `router_initial_admissible_candidate_count` | +6.5018 |
| `request_rate_rps` | +4.9866 |
| `router_initial_max_available_kv_bytes` | +4.9657 |

全係数は`scheduler_ridge_coefficients.csv`に保存した。

## Compute/prefill時間

Computeはraw token単位の明示的な線形式となった。

$$
\boxed{
\hat t_{compute}
=
\max\left(
0,
-96.717513
+0.1440215\,inputTokens
-0.1190117\,homeCachedPrefixTokens
\right)
}
$$

`home_cached_prefix_tokens`はrequest送信時にhome GPU上で利用可能なprefix token数である。
現在のCSVはpolicy適用後のreuseしか保持しないため、学習datasetでは同一scenario・requestの
policy間最大`reuse_prefix_toks`から再構成した。実運用ではsend時のhome cache lookup値を
直接入力する必要がある。

例えばinput 6,000、home cache 3,000 tokensなら、

$$
\hat t_{compute}
=-96.72+0.14402(6000)-0.11901(3000)
\approx410.4\;ms
$$

## 完全なTTFT式

以上をまとめると、point predictionは次式である。

$$
\boxed{
\widehat{TTFT}
=
p_{route}\left[\exp(clip(g(x),0.001642,10.878924))-1\right]
+\max(0,\gamma_0+\gamma^Tz)
+\max(0,-96.717513+0.1440215I-0.1190117C)
}
$$

$I$はinput tokens、$C$はhome cached prefix tokensである。policy one-hot項は簡略記法の
$\gamma^Tz$へ含めた。

## 信頼性評価

6,000 requests、7 independent scenariosをleave-one-scenario-outで評価した。

| Target | OOF metric |
|---|---:|
| Queue発生 | ROC-AUC 0.9964 |
| Queue発生 | PR-AUC 0.9754 |
| `t_route` | MAE 989.6 ms |
| `t_sched` | MAE 56.5 ms |
| `t_compute` | MAE 59.5 ms |
| `t_comm`省略 | MAE 2.9 ms |
| **TTFT** | **MAE 1,067.5 ms** |
| **TTFT** | **R² 0.356** |

Absolute TTFT errorの分布はp50 79.9 ms、p90 2,282 ms、p95 6,829 ms、p99 18,858 msだった。
81.9%のrequestsは500 ms以内、86.9%は1秒以内だった。

![TTFT式のOOF予測](../figures/ttft_formula/ttft_formula_oof.png)

この式はqueue発生と通常領域には一定の信頼性があるが、高負荷long-tailの時間量を過小予測
する。特に`input6000_rate5p0_reuse00` scenarioのMAEは3.74秒だった。したがって、容量制御の
発生リスク判定や中央値近傍のTTFT推定には使用できるが、p95/p99 SLAを保証する式としては
まだ不十分である。

## 係数・model artifact

- `numeric_feature_scaling.csv`: $\mu,\sigma$
- `route_event_logistic_coefficients.csv`: $\beta$
- `route_positive_tree_coefficients.json`: $g(x)$の全分岐・leaf係数
- `scheduler_ridge_coefficients.csv`: $\gamma$
- `compute_linear_coefficients.csv`: computeのraw係数
- `models/ttft_formula/ttft_formula.joblib`: fitted model bundle
- `analysis/ttft_formula/oof_predictions.csv`: 全held-out予測

## 入力例を用いた数式の追跡

以下は保存済みmodelへ実際に入力した4例である。Logisticとschedulerについては、各項を
`standardized value × coefficient`へ分解した。紙面に載せない小さい項も含む完全な加算表は
`analysis/ttft_formula/examples/`に保存した。

### 例1：低負荷

主要入力：

```text
input_tokens = 4000
home_cached_prefix_tokens = 0
request_rate_rps = 2.507
policy = NEAREST_MIGRATE_KV
router_initial_running_reqs = 5
router_initial_capacity_pressure = 0.3713
router_initial_slot_pressure = 0.046875
router_initial_admissible_candidate_count = 10
```

Queue発生logitの主要項は次の通りである。

$$
\begin{aligned}
L={}&-14.0132 &&\text{intercept}\\
&-2.2115 &&\text{required KV}\\
&-2.1112 &&\text{input tokens}\\
&-1.7973 &&\text{capacity pressure}\\
&-1.5509 &&\text{projected active KV}\\
&-1.5509 &&\text{available KV}\\
&-1.4733 &&\text{all remaining terms}\\
={}&-24.7085
\end{aligned}
$$

$$
p_{route}=\sigma(-24.7085)=1.8588\times10^{-11}
$$

Positive-tail treeは次の和になった。

$$
g(x)=8.324456+(-7.431511)=0.892945
$$

$$
t_{route,+}=e^{0.892945}-1=1.442311
$$

$$
\hat t_{route}=1.8588\times10^{-11}\times1.442311
\approx0.000000000027\;ms
$$

Scheduler式は、主要6項の和194.3080 ms、残りの項が−5.6959 msだった。

$$
\hat t_{sched}=\max(0,194.3080-5.6959)=188.6122\;ms
$$

Compute式は、

$$
\hat t_{compute}
=-96.7175+0.1440215(4000)-0.1190117(0)
=479.3685\;ms
$$

したがって、

$$
\boxed{
\widehat{TTFT}=0+188.6122+479.3685+0=667.9807\;ms
}
$$

### 例2：6,000 tokens、2,992 cached tokens

主要入力：

```text
input_tokens = 6000
home_cached_prefix_tokens = 2992
request_rate_rps = 3.326
policy = NEAREST_KV
router_initial_running_reqs = 6
router_initial_capacity_pressure = 0.6247
router_initial_admissible_candidate_count = 9
```

Queue発生logitは、

$$
L=-14.0132-0.3929+0.3504-0.3405+0.3224+0.3224+0.6807
=-13.0707
$$

最後の$+0.6807$は上に個別表示しなかった残り全項の和である。

$$
p_{route}=\sigma(-13.0707)=2.1061\times10^{-6}
$$

$$
g(x)=8.324456-0.919778=7.404678
$$

$$
t_{route,+}=e^{7.404678}-1=1642.6554\;ms
$$

$$
\hat t_{route}=2.1061\times10^{-6}\times1642.6554
=0.003460\;ms
$$

Schedulerは主要6項95.8840 ms、残り7.9694 msである。

$$
\hat t_{sched}=95.8840+7.9694=103.8534\;ms
$$

Computeはcacheによって356.0830 ms短縮される。

$$
\begin{aligned}
\hat t_{compute}
&=-96.7175+0.1440215(6000)-0.1190117(2992)\\
&=-96.7175+864.1290-356.0830\\
&=411.3286\;ms
\end{aligned}
$$

$$
\boxed{
\widehat{TTFT}=0.0035+103.8534+411.3286=515.1854\;ms
}
$$

### 例3：Capacity境界

主要入力：

```text
input_tokens = 6000
home_cached_prefix_tokens = 0
request_rate_rps = 4.733
policy = NEAREST_KV
router_initial_running_reqs = 10
router_initial_capacity_pressure = 0.9854
router_initial_slot_pressure = 0.085938
router_initial_admissible_candidate_count = 5
```

Logitの主要項を展開すると、

$$
\begin{aligned}
L={}&-14.0132 &&\text{intercept}\\
&+2.9822 &&\text{projected active KV}\\
&+2.9822 &&\text{available KV}\\
&+2.9256 &&\text{capacity pressure}\\
&+2.4829 &&\text{slot pressure}\\
&+2.4829 &&\text{running requests}\\
&-0.1001 &&\text{remaining terms}\\
={}&-0.257624
\end{aligned}
$$

available KV項が正なのは、標準化値と係数がともに負だからである。

$$
p_{route}=\sigma(-0.257624)=0.435948
$$

$$
g(x)=8.324456+0.158842=8.483298
$$

$$
t_{route,+}=e^{8.483298}-1=4832.3635\;ms
$$

$$
\hat t_{route}=0.435948\times4832.3635=2106.6587\;ms
$$

$$
\hat t_{sched}=75.9180+3.1517=79.0697\;ms
$$

$$
\hat t_{compute}=-96.7175+0.1440215(6000)=767.4115\;ms
$$

$$
\boxed{
\widehat{TTFT}=2106.6587+79.0697+767.4115=2953.1399\;ms
}
$$

### 例4：収容可能GPUなし

主要入力：

```text
input_tokens = 6000
home_cached_prefix_tokens = 0
request_rate_rps = 4.733
policy = NEAREST_KV
router_initial_running_reqs = 11
router_initial_capacity_pressure = 1.0862
router_initial_slot_pressure = 0.093750
router_initial_admissible_candidate_count = 0
```

$$
\begin{aligned}
L={}&-14.0132 &&\text{intercept}\\
&+4.4478 &&\text{admissible candidate count}\\
&+3.7561 &&\text{projected active KV}\\
&+3.7561 &&\text{available KV}\\
&+3.7014 &&\text{capacity pressure}\\
&+3.0230 &&\text{slot pressure}\\
&+0.7310 &&\text{remaining terms}\\
={}&5.402054
\end{aligned}
$$

admissible countは標準化値が負、係数も負なので、0 GPUsのとき正のlogit寄与になる。

$$
p_{route}=\sigma(5.402054)=0.995513
$$

$$
g(x)=8.324456+0.745152=9.069608
$$

$$
t_{route,+}=e^{9.069608}-1=8686.2176\;ms
$$

$$
\hat t_{route}=0.995513\times8686.2176=8647.2418\;ms
$$

Schedulerの主要6項は−16.4103 msだが、その他の項が+31.8006 msなので、

$$
\hat t_{sched}=\max(0,-16.4103+31.8006)=15.3903\;ms
$$

$$
\hat t_{compute}=767.4115\;ms
$$

$$
\boxed{
\widehat{TTFT}=8647.2418+15.3903+767.4115=9430.0436\;ms
}
$$

### 完全な計算トレース

各例の省略なしの計算値は以下に保存した。

- `examples/example_summary.csv`: 入力、各中間値、最終TTFT
- `examples/route_logistic_contributions.csv`: 全logistic項の`z × coefficient`
- `examples/route_tail_tree_trace.csv`: 100 treesすべての分岐pathとleaf寄与
- `examples/scheduler_contributions.csv`: 全scheduler項の`z × coefficient`

これらを再生成するスクリプトは`trace_ttft_formula_examples.py`である。

## 再現・予測

学習と係数export：

```bash
MPLCONFIGDIR=/tmp/llmservingsim-mpl PYTHONPYCACHEPREFIX=/tmp/llmservingsim-pycache LOKY_MAX_CPU_COUNT=8 python3 experiments/2026-07-16_ttft_component_regression/scripts/fit_ttft_formula.py
```

入力CSVへの適用：

```bash
python3 experiments/2026-07-16_ttft_component_regression/scripts/predict_ttft_formula.py \
  <INPUT_FEATURES_CSV> \
  <OUTPUT_PREDICTIONS_CSV>
```
