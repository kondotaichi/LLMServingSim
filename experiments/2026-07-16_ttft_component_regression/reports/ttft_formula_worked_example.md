# TTFT定式化の完全計算例

## 目的

本書は`ttft_formula.md`から独立したworked exampleである。1件のrequestについて、raw入力が
どのように標準化され、学習済み係数と掛け合わされ、最終的なTTFTへ変換されるかを数値で
追跡する。

対象は次のcache reuse requestである。

```text
input_tokens = 6000
output_tokens = 528
home_cached_prefix_tokens = 2992
request_rate_rps = 3.32623
policy = NEAREST_KV
router_initial_running_reqs = 6
router_initial_capacity_pressure = 0.624656
router_initial_admissible_candidate_count = 9
```

この例の最終予測は次の値になる。

$$
\boxed{\widehat{TTFT}=515.1854\;ms}
$$

以下でこの515.1854 msを最初から組み立てる。

## 1. 係数はどこから得たか

6,000 requestsを学習datasetとし、request送信時に取得可能な入力だけを使ってモデルをfitした。

- Queue発生：class-weighted Logistic regression、`C=0.1`
- 正値queue時間：absolute-error Gradient Boosting、100 trees、depth 2、learning rate 0.05
- Scheduler：Ridge regression、`alpha=1000`
- Compute：通常の2変数Linear regression

Logistic/Ridgeの係数はscikit-learnが次の正則化付き目的関数を最小化して得た学習結果であり、
手作業で設定した値ではない。汎化性能は7 scenariosのleave-one-scenario-outで評価した。

## 2. 標準化

数値入力$x_j$は、学習dataset全体から求めた平均$\mu_j$とscale$\sigma_j$を使い、

$$
z_j=\frac{x_j-\mu_j}{\sigma_j}
$$

へ変換する。例えばinput tokensは、

$$
z_{input}=\frac{6000-6300}{1307.67}=-0.229416
$$

Required KVは、

$$
z_{requiredKV}
=\frac{855638016-912268110.51}{171885073.39}
=-0.329465
$$

である。Policyは標準化せず、該当categoryを1、それ以外を0とする。

## 3. 全入力、標準化値、係数、寄与

`Logistic寄与`は$z_j\beta_j$、`Scheduler寄与`は$z_j\gamma_j$である。Interceptだけは
$z=1$として記載する。

| Term | Raw $x$ | Mean $\mu$ | Scale $\sigma$ | $z$ | Logistic $\beta$ | $z\beta$ | Scheduler $\gamma$ | $z\gamma$ ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `intercept` | — | — | — | 1.000000 | -14.013232 | -14.013232 | 60.427288 | 60.427288 |
| `input_tokens` | 6000 | 6300 | 1307.67 | -0.229416 | 1.200337 | -0.275376 | 7.513734 | -1.723769 |
| `output_tokens` | 528 | 652.51 | 98.2755 | -1.266948 | -0.016759 | 0.021233 | 0.540866 | -0.685249 |
| `home_cached_prefix_tokens` | 2992 | 899.2 | 1576.91 | 1.327152 | 0.029309 | 0.038898 | -17.524015 | -23.257037 |
| `request_rate_rps` | 3.32623 | 3.10687 | 0.748264 | 0.293157 | -0.102136 | -0.029942 | 4.986562 | 1.461845 |
| `arrival_offset_s` | 41.3174 | 52.1576 | 30.565 | -0.354662 | 0.134072 | -0.047550 | -4.342459 | 1.540104 |
| `interarrival_ms` | 69.9319 | 335.846 | 334.89 | -0.794034 | -0.129657 | 0.102952 | -3.490002 | 2.771179 |
| `global_arrivals_1s` | 2 | 3.05333 | 1.95529 | -0.538710 | -0.040623 | 0.021884 | 1.122698 | -0.604809 |
| `global_arrivals_5s` | 18 | 15.0145 | 5.63675 | 0.529649 | -0.107906 | -0.057152 | 3.800224 | 2.012785 |
| `home_arrivals_1s` | 1 | 0.293 | 0.549076 | 1.287618 | 0.078535 | 0.101123 | 50.733577 | 65.325466 |
| `home_arrivals_5s` | 1 | 1.52367 | 1.33321 | -0.392787 | 0.122117 | -0.047966 | 7.802179 | -3.064597 |
| `home_workload_share` | 0.1 | 0.105867 | 0.0278732 | -0.210477 | 0.288231 | -0.060666 | -8.111081 | 1.707195 |
| `router_initial_waiting_reqs` | 0 | 0.0178333 | 0.14554 | -0.122532 | 0.093046 | -0.011401 | 52.734341 | -6.461641 |
| `router_initial_running_reqs` | 6 | 5.403 | 3.22629 | 0.185042 | 1.742526 | 0.322441 | 0.493707 | 0.091357 |
| `router_initial_required_kv_bytes` | 8.55638e8 | 9.12268e8 | 1.71885e8 | -0.329465 | 1.192478 | -0.392880 | 7.634262 | -2.515221 |
| `router_initial_available_kv_bytes` | 4.49996e9 | 4.74760e9 | 3.01436e9 | -0.082153 | -2.397267 | 0.196943 | -2.471808 | 0.203067 |
| `router_initial_projected_active_kv_bytes` | 5.20933e9 | 4.96169e9 | 3.01436e9 | 0.082153 | 2.397267 | 0.196943 | 2.471808 | 0.203067 |
| `router_initial_capacity_pressure` | 0.624656 | 0.604983 | 0.315457 | 0.062363 | 2.426229 | 0.151307 | 2.861097 | 0.178427 |
| `router_initial_slot_pressure` | 0.0546875 | 0.0500234 | 0.0252054 | 0.185042 | 1.742526 | 0.322441 | 0.493707 | 0.091357 |
| `router_initial_admissible_candidate_count` | 9 | 8.36 | 2.24352 | 0.285266 | -1.193628 | -0.340501 | 6.501803 | 1.854740 |
| `router_initial_total_waiting_reqs` | 0 | 0.201167 | 0.493658 | -0.407502 | -0.028946 | 0.011795 | 3.516373 | -1.432928 |
| `router_initial_max_waiting_reqs` | 0 | 0.187833 | 0.44783 | -0.419430 | 0.040501 | -0.016987 | -1.619341 | 0.679200 |
| `router_initial_total_running_reqs` | 60 | 53.2447 | 22.6761 | 0.297905 | -0.200975 | -0.059871 | -0.201466 | -0.060018 |
| `router_initial_max_running_reqs` | 11 | 8.6535 | 2.52826 | 0.928108 | -0.327820 | -0.304252 | -1.773141 | -1.645666 |
| `router_initial_min_available_kv_bytes` | 9.80337e7 | 1.85676e9 | 2.34362e9 | -0.750430 | -0.422667 | 0.317182 | -1.794255 | 1.346462 |
| `router_initial_max_available_kv_bytes` | 9.70928e9 | 7.91346e9 | 1.93562e9 | 0.927774 | 0.216292 | 0.200670 | 4.965710 | 4.607058 |
| `router_initial_min_capacity_pressure` | 0.0881258 | 0.278917 | 0.203352 | -0.938233 | -0.108230 | 0.101545 | -4.203556 | 3.943914 |
| `router_initial_max_capacity_pressure` | 1.07803 | 0.902723 | 0.248209 | 0.706284 | 0.496089 | 0.350380 | 2.289391 | 1.616959 |
| `policy_NEAREST_KV` | 1 | 0 | 1 | 1.000000 | 0.129384 | 0.129384 | -4.757168 | -4.757168 |
| `policy_NEAREST_MIGRATE` | 0 | 0 | 1 | 0.000000 | -0.044772 | 0 | 3.571026 | 0 |
| `policy_NEAREST_MIGRATE_KV` | 0 | 0 | 1 | 0.000000 | -0.085716 | 0 | 1.186142 | 0 |

## 4. Queue発生確率

上表の`$z\beta$`列をすべて加算する。

$$
L=\sum_jz_j\beta_j=-13.070654
$$

例として、running requestsの寄与は、

$$
\frac{6-5.403}{3.22629}\times1.742526
=0.185042\times1.742526
=0.322441
$$

Admissible candidate countの寄与は、

$$
\frac{9-8.36}{2.24352}\times(-1.193628)
=0.285266\times(-1.193628)
=-0.340501
$$

である。Logitをsigmoidへ通す。

$$
p_{route}
=\frac{1}{1+e^{-L}}
=\frac{1}{1+e^{13.070654}}
=0.000002106135
$$

百分率では0.0002106%である。

## 5. Queueが発生した場合の時間

正値queue時間は100本のdepth-2 treesで推定する。

$$
g(x)=g_0+\sum_{m=0}^{99}0.05v_{m,leaf_m(x)}
$$

初期値は、

$$
g_0=8.324455772
$$

である。最初の10 treesは次のleafを選んだ。

| Tree | 選択された主な条件 | Weighted leaf contribution |
|---:|---|---:|
| 0 | `policy_NEAREST_KV > 0.5`かつ`home_workload_share <= 0.088496` | −0.005125 |
| 1 | 同上 | −0.004869 |
| 2 | 同上 | −0.004625 |
| 3 | 同上 | −0.004394 |
| 4 | `policy_NEAREST_KV > 0.5`かつ`home_arrivals_5s <= 0.73232` | +0.002853 |
| 5 | `home_workload_share <= 1.22459`かつ`required_kv <= 1.36036` | −0.002408 |
| 6 | `home_workload_share > -0.748628`かつ`min_pressure <= 1.26843` | −0.001485 |
| 7 | `home_arrivals_5s <= 0.73232`かつ`arrival_offset <= 1.11494` | −0.020214 |
| 8 | `home_arrivals_5s <= 0.73232`かつ`required_kv <= 1.33596` | −0.000545 |
| 9 | `policy_NEAREST_KV > 0.5`かつ`home_arrivals_5s <= 1.48239` | +0.009140 |

Tree条件内の数値はraw値ではなくz-scoreである。例えばtree 0の
`home_workload_share=-0.210477`は、raw 0.1を標準化した値である。

100 treesを10本ずつ加えた小計は次の通りである。

| Trees | Contribution sum |
|---|---:|
| 0–9 | −0.031671 |
| 10–19 | −0.075315 |
| 20–29 | −0.134523 |
| 30–39 | −0.035696 |
| 40–49 | −0.124075 |
| 50–59 | −0.118097 |
| 60–69 | −0.045693 |
| 70–79 | −0.105035 |
| 80–89 | −0.124717 |
| 90–99 | −0.124957 |
| **合計** | **−0.919778** |

したがって、

$$
g(x)=8.324456-0.919778=7.404678
$$

clip範囲$[0.001642,10.878924]$内なので値は変わらない。

$$
t_{route,+}=e^{7.404678}-1=1642.655374\;ms
$$

これはqueueが発生したという条件下の予測時間である。期待router時間は発生確率を掛ける。

$$
\hat t_{route}
=p_{route}t_{route,+}
=0.000002106135\times1642.655374
=0.003459653\;ms
$$

## 6. Scheduler時間

上表の`$z\gamma$`列をすべて加算する。

$$
S=\sum_jz_j\gamma_j=103.853367808\;ms
$$

主要項を確認すると、

$$
\begin{aligned}
S &= 60.427288 && \text{intercept}\\
&\quad + 65.325466 && \text{home arrivals in 1 s}\\
&\quad - 23.257037 && \text{cached prefix}\\
&\quad - 6.461641 && \text{home waiting requests}\\
&\quad - 4.757168 && \text{NEAREST\_KV policy}\\
&\quad + 4.607058 && \text{maximum available KV}\\
&\quad + 7.969402 && \text{remaining terms}\\
&= 103.853368
\end{aligned}
$$

予測式は負値を0へclipするが、この例は正なので、

$$
\hat t_{sched}=\max(0,S)=103.853368\;ms
$$

となる。

## 7. Compute時間

Computeだけは標準化せずraw token数へ直接係数を掛ける。

$$
\hat t_{compute}
=\max(0,-96.717513+0.144021508I-0.119011686C)
$$

$I=6000$、$C=2992$なので、

$$
0.144021508\times6000=864.129046
$$

$$
-0.119011686\times2992=-356.082964
$$

$$
\hat t_{compute}
=-96.717513+864.129046-356.082964
=411.328570\;ms
$$

## 8. Communication時間

本point modelはrequest送信後のredirect結果を入力に使わない。対象datasetの92.6%で
communication componentが0であり、これを0とするOOF MAEは2.88 msだったため、

$$
\hat t_{comm}=0
$$

とした。

## 9. 最終TTFT

各componentを加算する。

$$
\begin{aligned}
\widehat{TTFT}
&=\hat t_{route}+\hat t_{sched}+\hat t_{compute}+\hat t_{comm}\\
&=0.003459653+103.853367808+411.328569719+0\\
&=515.185397180\;ms
\end{aligned}
$$

したがって最終結果は、

$$
\boxed{\widehat{TTFT}=515.1854\;ms}
$$

となる。このrequestに対応するsimulation観測値は420.5908 msであり、この1点のabsolute
errorは94.5946 msだった。これは学習精度の評価値ではなく、具体例1点の差である。

## 10. 再現用ファイル

- `numeric_feature_scaling.csv`: 全$\mu,\sigma$
- `route_event_logistic_coefficients.csv`: 全$\beta$
- `route_positive_tree_coefficients.json`: 全tree/node/leaf係数
- `scheduler_ridge_coefficients.csv`: 全$\gamma$
- `compute_linear_coefficients.csv`: Compute係数
- `examples/route_logistic_contributions.csv`: この例を含む全Logistic積
- `examples/route_tail_tree_trace.csv`: 100 tree paths
- `examples/scheduler_contributions.csv`: 全Scheduler積
- `examples/example_summary.csv`: component和と最終TTFT

再生成コマンド：

```bash
python3 experiments/2026-07-16_ttft_component_regression/scripts/trace_ttft_formula_examples.py
```
