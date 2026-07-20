# Post-routing TTFT component支配要因解析

<!-- Report revision: component formulation added on 2026-07-19 -->

## 1. 使用データ

`experiments/`以下の全`requests.csv`を走査し、300 requestが揃い、E2E TTFT列を持つA/B/C runを使用した。

```text
Runs: 53
Requests: 15,900
Independent scenarios: 18
Policies: A=18, B=18, C=17 runs
新scheduler状態を持つruns: 11
```

Input tokensは512、2000、4000、6000、10000、reuseは0%、約25%、約50%、request rateは約1.67～5.01 req/sを含む。

Component和によるE2E TTFT再構成の最大誤差は`2.91e-11 ms`であり、数値誤差の範囲だった。

## 2. 解析方法

二つのPost-routingモデルを分離した。

### Commonモデル

全53 runsで共通して存在する特徴量を使用した。

- Request demand
- Nominal reuse
- Realized reuse
- Offered load
- Home-cell load
- Policy
- Routing result
- KV movement

### State診断モデル

新しい状態列を持つ11 runs、3,300 requestsだけを使用した。

- Common特徴量
- Scheduler waiting/running/decode数
- Scheduled prefill/decode tokens
- Batch sequence数
- Prefill tokens ahead

`router_capacity_wait_ns`はrouter queue targetと実質的に同じ実現待ち時間なので、説明変数から除外した。

支配要因の主指標は、feature groupを除いて再学習した際のheld-out MAE増加である。

```text
Group importance
  = MAE(without group) - MAE(full model)
```

同一scenarioのA/B/Cを同じfoldへ置くleave-one-scenario-out評価を使用し、scenario単位bootstrapで95%信頼区間を計算した。

## 3. Component分布

| Component | Mean | Median | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|
| Router queue | 2,697 ms | 0 ms | 17,561 ms | 57,212 ms | 109,332 ms |
| Scheduler queue | 30.7 ms | 11.8 ms | 114 ms | 535 ms | 2,683 ms |
| KV transfer | 7.47 ms | 0 ms | 0 ms | 317 ms | 528 ms |
| Compute/prefill | 431 ms | 392 ms | 1,329 ms | 1,353 ms | 1,693 ms |
| E2E TTFT | 3,166 ms | 404 ms | 18,703 ms | 58,430 ms | 110,690 ms |

Input size別の平均：

| Input | Router queue | Scheduler queue | KV transfer | Compute | E2E |
|---:|---:|---:|---:|---:|---:|
| 512 | 0 ms | 8.6 ms | 0 ms | 48.6 ms | 57.1 ms |
| 2000 | 0 ms | 13.0 ms | 0 ms | 166 ms | 179 ms |
| 4000 | 6.5 ms | 26.3 ms | 0 ms | 433 ms | 465 ms |
| 6000 | 1,181 ms | 31.9 ms | 11.0 ms | 436 ms | 1,660 ms |
| 10000 | 11,022 ms | 69.3 ms | 24.7 ms | 1,006 ms | 12,122 ms |

Input 512～4000ではcomputeが中心だが、6000からrouter queueが増え、10000ではrouter queueがE2Eを支配する。

全request中、redirectは813件、KV移動は312件、router queue正値は2,516件だった。

## 4. モデル性能

| Feature set | Target | OOF MAE | OOF R² |
|---|---|---:|---:|
| Common | Compute | 33.9 ms | 0.978 |
| Common | Scheduler queue | 37.6 ms | 0.099 |
| Common | Router queue | 3,662 ms | 0.215 |
| Common | E2E TTFT | 3,733 ms | 0.248 |
| State | Compute | 57.2 ms | 0.047 |
| State | Scheduler queue | 21.9 ms | 0.429 |
| State | Router queue | 6.9 ms | -0.002 |
| State | E2E TTFT | 77.5 ms | 0.181 |

State subsetはinput 4000、2.5 req/s付近の低負荷4 scenariosだけなので、CommonモデルとのMAEを直接比較しない。

## 5. Compute/prefillの支配要因

Commonモデルで95%信頼区間が0を跨がず、安定して重要だったgroupは`request_demand`だった。

```text
Request demand ablation MAE increase:
  mean 7.90 ms
  95% CI [3.28, 12.51] ms
```

標準化係数では`realized_uncached_tokens`が最大の正係数だった。Input tokens、minimum prefill chunks、nominal reuse、realized reuseは強く共線なので、個別係数よりgroup importanceを優先する。

State診断モデルでは`scheduler_state`を除くとcompute MAEが平均10.88 ms増加した。

```text
Scheduler state:
  10.88 ms
  95% CI [8.96, 12.80] ms
```

これはcomputeがuncached token量だけでなく、同一batchのsequence数やdecode/prefill構成にも依存することを示す。ただしState subsetは4 scenariosだけなので暫定結果である。

### Computeの判断

1. 第一支配要因：Request demand、特に実際にcomputeするuncached tokens
2. 第二候補：Scheduler batch構成
3. Realized reuseはcomputeを減らす方向だが、request demandとの共線性が強く、独立重要度の95% CIは0を跨いだ

## 6. Scheduler queueの支配要因

CommonモデルだけではOOF R²が0.099で、安定した支配groupを特定できなかった。

State診断モデルでは次の二つが95% CIを跨がなかった。

```text
Home-cell load:
  MAE increase 3.74 ms
  95% CI [0.59, 6.61] ms

Scheduler state:
  MAE increase 1.97 ms
  95% CI [0.11, 4.78] ms
```

標準化係数ではbatch sequence数、scheduled decode tokens、scheduled prefill tokensが大きかったが、これらはtoken budget制約によって相互依存する。そのため符号を単独で因果解釈しない。

### Scheduler queueの判断

1. 第一候補：Home-cellへの局所arrival集中
2. 第二候補：初回schedule時のbatch・decode・prefill状態
3. 単純なinput sizeやpolicyだけではscheduler queueを十分説明できない

## 7. Router queueの支配要因

Router queueは中央値0、p99 57秒というzero-inflated long-tailである。Common RidgeのOOF MAEは3.66秒、R²は0.215に留まった。

Common modelのgroup ablationでは`request_demand`が平均57.5 msで最大だったが、95% CIは`[-238, 354] ms`と広く0を跨いだ。Routing resultやpolicyも安定した正のimportanceにはならなかった。

原因：

- Router queueの大部分が一部の6000/10000高負荷scenarioへ集中する
- Capacity snapshotが成功requestでは保存されていない
- Input、request rate、reuse、capacity regimeが完全なfactorialではない
- 1本のRidgeでqueueなしと数十秒tailを同時に扱っている

### Router queueの判断

現データから「inputが長いほどrouter queueが支配的になる」ことは明確だが、request単位の直接的支配要因はまだ確定できない。

次の解析には、全requestのrouting decision時点で以下を保存する必要がある。

- Required KV bytes
- Available KV bytes
- Projected active KV bytes
- Running/waiting request数
- Capacity pressure

そのうえで`queue発生Logistic + 正値queue log-Ridge`の二段階モデルを使用する。

### Router capacity loggingの改訂

2026-07-19に、capacity判定失敗時だけ残していた旧ログを見直した。改訂後は全requestについて、次の二時点を別々に保存する。

1. `router_initial_*`：最初のrouting試行時点におけるhome GPUと候補GPU群の状態
2. `router_decision_*`：最終的な移動先GPUへrequestを渡す直前の状態

主な追加列：

- Required、available、projected active、budget KV bytes
- Selected GPUのwaiting/running request数
- KV capacity pressureとsequence slot pressure
- 全候補中のadmissible GPU数
- 候補GPU群のwaiting/running合計と最大値
- 候補GPU群のavailable KVとcapacity pressureの最小・最大値
- Capacity retry回数
- 最初にblockされた理由とGPU

Capacity pressureは次式で定義する。

$$
pressure_i
=
\frac{projectedActiveKV_i + requiredKV_i}{kvBudget_i}
$$

1以下ならKV budget上は収容可能で、1を超えると候補requestを含めた予約量がbudgetを超える。Sequence slotについても、`(running requests + 1) / max_num_seqs`を別列で保存する。

この改訂以前に生成したPhase 1の11 runsでは、新しい列は存在せず、旧capacity列も3,300 requests中4件しか値を持たない。したがって既存結果へ遡及はせず、支配要因解析に使うには対象runを再実行する必要がある。

## 8. KV transferの支配要因

KV transferは312 requestsで発生した。時間は次の解析式で決定される。

```text
Migration bytes / bandwidth
+ distance latency
+ CPU staging
```

したがって、KV transfer時間の支配要因は次の順で構造的に決まる。

1. KVを実際に移動したか
2. Migration tokens/bytes
3. Network bandwidth
4. Distance latency
5. CPU staging bandwidth/latency

回帰係数による順位より、解析式による再構成とE2E占有率を使用する。

Policy CにおけるKV transferの平均request-level E2E比率は全scenario平均で約0.87%だった。KV transfer単体より、それに伴うrouting/queue状態変化の方がE2Eへ大きく影響する。

## 9. E2E TTFTの支配要因

Component shareを全scenarioで平均すると次の通りだった。

| Policy | Router queue | Scheduler queue | KV transfer | Compute |
|---|---:|---:|---:|---:|
| A | 13.36% | 6.47% | 0% | 80.17% |
| B | 12.12% | 6.74% | 0% | 81.14% |
| C | 12.42% | 6.71% | 0.87% | 80.00% |

これはscenarioを同じ重みで平均した値であり、高負荷requestだけを見ればrouter queue比率は大幅に高くなる。

E2Eの支配componentはregimeで変化する。

```text
512～4000:
  Compute支配

6000:
  ComputeからRouter queueへの遷移領域

10000:
  Router queue支配
```

## 10. 結論

現時点で安定して確認できた支配要因：

| Component | 支配要因 | 確度 |
|---|---|---|
| Compute/prefill | Request demand / uncached tokens | 高い |
| Compute/prefill | Scheduler batch構成 | 暫定、4 scenarios |
| Scheduler queue | Home-cell load | 暫定、4 scenarios |
| Scheduler queue | Scheduler state | 暫定、4 scenarios |
| KV transfer | KV移動有無とmigration bytes | 解析式で確定 |
| Router queue | Capacity pressureと考えられるがsnapshot不足 | 未確定 |
| E2E TTFT | 低負荷はcompute、高負荷はrouter queue | 高い |

最も重要な追加作業は、router capacity snapshotを全requestへ保存することである。Schedulerについては新しい状態列が有効である兆候が確認できたため、状態列を持つinput/rate/reuse条件を増やす価値がある。

## 11. 入力パラメータからTTFTを求める定式化

### 11.1 基本方針

TTFT全体を1本の線形回帰で直接予測するのではなく、requestごとに4 componentを個別に推定し、最後に合計する。

$$
\widehat{T_i^{\mathrm{TTFT}}}
=
\widehat{Q_i^{\mathrm{router}}}
+
\widehat{Q_i^{\mathrm{scheduler}}}
+
\widehat{T_i^{\mathrm{KV}}}
+
\widehat{T_i^{\mathrm{compute}}}
+
\widehat{T_i^{\mathrm{other}}}
$$

ここで、$i$はrequest、$Q$はqueue待ち時間、$T$は処理時間を表す。`other`はRTTなど、現在の4 componentへ含まれない小さな通信時間である。

この分解には次の利点がある。

1. Componentごとに異なる分布を扱える
2. 各componentの支配要因を個別に解釈できる
3. KV transferのような決定式に近い成分を、無理に線形回帰へ含めずに済む
4. Input sizeや負荷によるボトルネックの切り替わりを表現できる

### 11.2 Post-routingモデルの入力

本解析の目的はrouting前の予測ではなく、routing後の実現状態を用いてTTFTの支配要因を診断することである。入力を二種類に分ける。

Routing前から分かる入力：

$$
x_i^{\mathrm{static}}
=
[P_i, O_i, \lambda, r_i^{\mathrm{nominal}}, policy_i, home_i]
$$

- $P_i$：input tokens
- $O_i$：output tokens
- $\lambda$：request rate
- $r_i^{\mathrm{nominal}}$：設定上のreuse率
- $policy_i$：A、B、Cのrouting policy
- $home_i$：requestが属するhome GPU cell

Routing後に確定する入力：

$$
x_i^{\mathrm{post}}
=
[redirect_i, destination_i, move_i, P_i^{\mathrm{moved}},
P_i^{\mathrm{reused}}, schedulerState_i, batchState_i]
$$

- $redirect_i$：実際にredirectされたか
- $destination_i$：実際の移動先GPU
- $move_i$：KVを実際に移動したか
- $P_i^{\mathrm{moved}}$：移動したKV tokens
- $P_i^{\mathrm{reused}}$：実際にreuseできたtokens
- $schedulerState_i$：到着時または初回schedule時のwaiting、running、decode状態
- $batchState_i$：参加batchのsequence数、prefill/decode token構成

したがって今回構築する診断モデルは次の形になる。

$$
\widehat{T_i^{\mathrm{TTFT}}}
=
f(x_i^{\mathrm{static}}, x_i^{\mathrm{post}})
$$

Routing前予測では使用できない実現値を含むため、このモデルの精度をarrival-timeモデルの精度と混在させない。

### 11.3 Compute/prefill

実際にcomputeが必要なinput tokensを次のように定義する。

$$
P_i^{\mathrm{effective}}
=
P_i - P_i^{\mathrm{reused}}
$$

基本モデルは次の形とする。

$$
\widehat{T_i^{\mathrm{compute}}}
=
\beta_0
+ \beta_1 P_i^{\mathrm{effective}}
+ \beta_2 O_i
+ \beta_3 N_i^{\mathrm{batch}}
+ \beta_4 D_i^{\mathrm{batch}}
+ \beta_5 C_i^{\mathrm{prefill,batch}}
$$

- $N_i^{\mathrm{batch}}$：参加batchのsequence数
- $D_i^{\mathrm{batch}}$：同じbatchに含まれるdecode tokens
- $C_i^{\mathrm{prefill,batch}}$：同じbatchでscheduleされたprefill tokens

現データでは$P_i^{\mathrm{effective}}$を含むrequest demandが第一支配要因である。Batch構成は第二候補だが、状態列を持つscenarioが少ないため暫定的な位置付けとする。

### 11.4 KV transfer

KV transferは回帰よりも、移動量と帯域に基づく決定式として扱う。

$$
\widehat{T_i^{\mathrm{KV}}}
=
M_i
\left(
L_{\mathrm{fixed}}
+
\frac{S_i^{\mathrm{KV}}}{B_{\mathrm{effective}}}
\right)
$$

- $M_i$：KVを移動した場合は1、それ以外は0
- $S_i^{\mathrm{KV}}$：移動するKVのbyte数
- $B_{\mathrm{effective}}$：実効転送帯域
- $L_{\mathrm{fixed}}$：伝搬時間やstaging latencyを含む固定時間

KV byte数は概念的には次式で表せる。

$$
S_i^{\mathrm{KV}}
=
P_i^{\mathrm{moved}}
\times N_{\mathrm{layers}}
\times 2
\times N_{\mathrm{KV\ heads}}
\times D_{\mathrm{head}}
\times Bytes_{\mathrm{KV\ element}}
$$

係数2はKeyとValueの二つを表す。実際の実装ではtensor parallelism、block丸め、staging経路など、simulatorのmemory modelと同じ計算を使用する。

### 11.5 Scheduler queue

Scheduler queueはzero-inflatedであるため、queue発生有無と正値の待ち時間を分離した二段階モデルを使用する。

第一段階では、待ちが発生する確率をLogistic回帰で推定する。

$$
p_i^{\mathrm{scheduler}}
=
P(Q_i^{\mathrm{scheduler}} > 0)
=
\sigma(\gamma^{\mathsf{T}}x_i^{\mathrm{scheduler}})
$$

第二段階では、待ちが発生したrequestだけを使い、正値queueを`log1p`変換して推定する。

$$
\log(1 + Q_i^{\mathrm{scheduler}})
=
\delta^{\mathsf{T}}x_i^{\mathrm{scheduler}}
$$

最終的な期待待ち時間は次のように求める。

$$
\widehat{Q_i^{\mathrm{scheduler}}}
=
p_i^{\mathrm{scheduler}}
\times
E[Q_i^{\mathrm{scheduler}} \mid Q_i^{\mathrm{scheduler}} > 0]
$$

主な説明変数は、home-cell load、到着時のwaiting/running数、prefill tokens ahead、batch内のprefill/decode tokens、token budget使用率、input/output tokensとする。

### 11.6 Router queue

Router queueも中央値0でtailが非常に長いため、Scheduler queueと同じ二段階構造を用いる。

$$
p_i^{\mathrm{router}}
=
P(Q_i^{\mathrm{router}} > 0)
=
\sigma(\alpha^{\mathsf{T}}x_i^{\mathrm{router}})
$$

$$
\log(1 + Q_i^{\mathrm{router}})
=
\theta^{\mathsf{T}}x_i^{\mathrm{router}}
\quad
(Q_i^{\mathrm{router}} > 0)
$$

$$
\widehat{Q_i^{\mathrm{router}}}
=
p_i^{\mathrm{router}}
\times
E[Q_i^{\mathrm{router}} \mid Q_i^{\mathrm{router}} > 0]
$$

現時点ではinput/output tokens、request rate、home-cell load、policy、redirect/handoff実現値、reuse量、KV移動量を使用できる。

ただし、個々のrequestのrouter待ちを説明するには、routing decision時点の次の情報が不足している。

- 各GPUのrequired/available/projected KV bytes
- 各GPUのrunning/waiting request数
- Redirect可能な候補GPU数
- 候補GPUごとのcapacity pressure
- Capacityが解放されるまでの先行request量

そのため、現時点のRouterモデルはcapacity regimeの判別には使えるが、request単位の待ち時間推定は暫定モデルとして扱う。

### 11.7 相互作用とボトルネック切り替え

Input sizeが4000から6000、10000へ増えると、compute支配からrouter queue支配へ切り替わる。これを表すため、線形項に加えて少なくとも次の相互作用を検討する。

$$
P_i \times \lambda,
\qquad
P_i \times r_i^{\mathrm{realized}},
\qquad
redirect_i \times homeLoad_i,
\qquad
M_i \times P_i^{\mathrm{moved}}
$$

単一の線形モデルで境界を表現できない場合は、input sizeまたはcapacity pressureによるpiecewise linear modelも比較する。

### 11.8 支配要因の判定方法

個別の標準化係数は、input tokens、reuse、uncached tokensなどの共線性によって不安定になる。支配要因はfeature groupをまとめて除去したときのOOF MAE増加を主指標とする。

$$
\Delta MAE_g
=
MAE_{\mathrm{without\ group}\ g}
-
MAE_{\mathrm{full}}
$$

$\Delta MAE_g$が大きいほど、そのfeature groupが予測と説明に重要である。Scenario単位bootstrapの95%信頼区間が0を跨がない場合に、安定した支配要因と判定する。

評価では以下を比較する。

1. 単純なcomponent別Ridge回帰
2. `log1p` component回帰
3. Router/Scheduler queueの二段階モデル
4. 相互作用を追加したモデル
5. Capacity境界を分割したpiecewise linear model

同一scenarioのA/B/Cは同じfoldへ入れ、未知のワークロード条件に対する汎化性能を評価する。

### 11.9 現時点での実現可能性

| Component | 定式化の状態 | 次に必要なこと |
|---|---|---|
| Compute/prefill | 現データで構築可能 | State付きscenarioの追加でbatch効果を確認 |
| KV transfer | 解析式で構築可能 | Simulator計算とのbyte単位照合 |
| Scheduler queue | 二段階モデルを構築可能 | State付きscenarioを負荷・input方向へ拡張 |
| Router queue | 暫定的な二段階モデルを構築可能 | 全requestのcapacity snapshotが必要 |
| E2E TTFT | Component和として構築可能 | 各componentモデルのOOF予測を合成して評価 |

したがって、Compute、KV transfer、Scheduler queueは現データから定式化を進められる。Router queueは暫定モデルを作成し、capacity snapshotを持つ追加データが揃った段階で更新する。

## 12. 出力

### Data

- `analysis/post_routing/run_inventory.csv`
- `analysis/post_routing/canonical_requests.csv`
- `analysis/post_routing/component_shares.csv`
- `analysis/post_routing/model_metrics.csv`
- `analysis/post_routing/standardized_coefficients.csv`
- `analysis/post_routing/group_ablation_importance.csv`
- `analysis/post_routing/bootstrap_importance.csv`
- `analysis/post_routing/oof_predictions.csv`

### Figures

- `figures/post_routing/component_share_all_experiments.png`
- `figures/post_routing/group_ablation_common.png`
- `figures/post_routing/group_ablation_state.png`
