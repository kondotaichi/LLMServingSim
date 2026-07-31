# TTFT定式化の総括

## 1. この文書の目的

本書は、これまで行ってきたTTFT（Time To First Token）の定式化を、個別の実験報告ではなく、ひとつの体系として整理したものである。対象は次の3段階である。

1. 実測TTFTを構成要素へ分解する。
2. request送信時に観測可能な情報から、各構成要素とTTFTを予測する。
3. 予測値を用いて、routing先やPP度などの構成を選択する。

この3つは目的が異なる。特に、実測値の分解は会計上の恒等式である一方、送信時予測式は誤差を持つ統計モデルであり、routingやPP選択式は複数の反実仮想を比較する意思決定式である。本書ではこれらを明確に分ける。

## 2. TTFTの定義と観測上の分解

request $i$ の送信時刻を $a_i$、最初のtokenが利用可能になる時刻を $f_i$ とすると、TTFTは

$$
TTFT_i = f_i-a_i
$$

である。これをシミュレータで観測する時間区間に対応させると、基本分解は

$$
\boxed{
TTFT_i
=t_{\mathrm{route},i}
+t_{\mathrm{sched},i}
+t_{\mathrm{compute},i}
+t_{\mathrm{comm},i}
}
$$

となる。

| 項 | 意味 |
|---|---|
| $t_{\mathrm{route}}$ | request送信後、routing先が確定して対象schedulerへ渡されるまでの待ち時間。capacity retryやhome待機を含む。 |
| $t_{\mathrm{sched}}$ | 対象schedulerへ到着してから、最初のprefill実行に入るまでのqueue待ち時間。 |
| $t_{\mathrm{compute}}$ | first token生成に必要なprefillおよび最初のdecodeに対応する計算時間。 |
| $t_{\mathrm{comm}}$ | request migration、KV migration、ネットワークRTT、PP stage間通信など、明示的に計上する通信時間。 |

この式は「実測TTFTの内訳」を表す。二重計上を避けるため、ある待ち時間をrouteとschedulerの両方へ含めてはならない。また、実験CSVのtimestamp定義が変わる場合は、この恒等式が成立するかを最初に確認する必要がある。

## 3. 送信時情報だけを用いる予測式

request送信時点で既知の特徴量を $x_i$、routing policyを $\pi_i$ とする。予測TTFTは、観測上の分解に合わせて

$$
\boxed{
\widehat{TTFT}_i(x_i,\pi_i)
=\hat t_{\mathrm{route},i}
+\hat t_{\mathrm{sched},i}
+\hat t_{\mathrm{compute},i}
+\hat t_{\mathrm{comm},i}
}
$$

と構成した。初期データでは通信項のOOF MAEが2.88 msと小さかったため、point predictionでは $\hat t_{\mathrm{comm}}=0$ とした。ただし、KV migrationやPP通信が意思決定を左右する現在の用途では、通信項を候補別に明示的に足す必要がある。

### 3.1 入力特徴量

数値特徴量は学習データの平均 $\mu_j$ とscale $\sigma_j$ を用いて

$$
z_j=\frac{x_j-\mu_j}{\sigma_j}
$$

へ標準化する。特徴量は概ね次のグループに分かれる。

| グループ | 主な特徴量 | 表すもの |
|---|---|---|
| request | input/output tokens、home cached prefix tokens | request自身の計算量、KV需要、再利用可能量 |
| 到着過程 | request rate、interarrival、直近1秒・5秒のarrival数 | offered load、burst、局所的な到着偏り |
| candidate GPU | waiting/running requests、required/available/projected KV bytes | 候補のscheduler負荷とKV収容余力 |
| 圧力 | capacity pressure、slot pressure | 新規requestを含めたadmission余裕 |
| system全体 | admissible候補数、全候補のwaiting/running合計、KV余力のmin/max | 逃げ先の有無と全体混雑 |
| policy | `NEAREST_KV`、`NEAREST_MIGRATE`、`NEAREST_MIGRATE_KV` | home待機、cold redirect、KV付きredirectの違い |

容量圧力は代表的には

$$
q_{\mathrm{cap}}
=\frac{M_{\mathrm{active}}^{\mathrm{projected}}+M_{\mathrm{required}}}
{M_{\mathrm{KV,budget}}}
$$

である。$q_{\mathrm{cap}}>1$ は、既存active requestsの将来使用量と新規requestの必要量を合わせるとKV budgetを超えることを意味する。

### 3.2 Routeとschedulerを統合したwaitモデル

旧式はrouter待ちを「発生確率」と「発生時の時間」に分け、$p_{\mathrm{route}}\hat t_{\mathrm{route},+}$を期待時間としていた。しかし、この積は低確率・巨大損失を強く圧縮する。実際には1.4–11.4秒待ったrequestを0.14–0.64秒と見積もるcaseがあり、候補比較やdeadline判定に使う単一スコアとして不自然だった。また、route待ちとscheduler待ちはrequestから見ると連続したadmission待ちであり、境界はシミュレータ内部の会計上の区分に依存する。

そこでv2では、予測対象を

$$
\boxed{
t_{\mathrm{wait}}
=t_{\mathrm{route}}+t_{\mathrm{sched}}
}
$$

へ統合する。waitは非負で右に長い分布を持つため、目的変数を

$$
y_{\mathrm{wait}}=\log(1+t_{\mathrm{wait}})
$$

へスケーリングし、送信時特徴量から単一の関数$f_{\mathrm{wait}}(x,\pi)$を学習する。

$$
\hat y_{\mathrm{wait}}=f_{\mathrm{wait}}(x,\pi)
$$

$$
\boxed{
\hat t_{\mathrm{wait}}
=\exp\!\left(
\operatorname{clip}(\hat y_{\mathrm{wait}},b_{\min},b_{\max})
\right)-1
}
$$

採用する$f_{\mathrm{wait}}$は、標準化した数値特徴量とpolicy one-hotを入力する単一のGradient Boosting Regressorである。検証設定はHuber loss、100 trees、learning rate 0.05、depth 2、minimum leaf 20とした。$p_{\mathrm{route}}$はTTFT計算から除外する。必要ならredirect発生の説明・診断指標として別途保持できるが、時間との積は取らない。

### 3.3 Compute/prefill時間

Compute項はinput tokens $I$ とhomeで再利用可能なprefix tokens $C$ を使う明示的な線形式である。

$$
\boxed{
\hat t_{\mathrm{compute}}
=\max\left(
0,
-96.717513
+0.1440215 I
-0.1190117 C
\right)
}
$$

例えば $I=6000$、$C=3000$ なら約410.4 msである。この式は「実際に選択したpolicy適用後のreuse量」ではなく、routing判断時にcandidate上で利用できるprefix量を入力すべきである。

### 3.4 v2の中心式

以上より、v2のpoint predictionは

$$
\boxed{
\widehat{TTFT}
=\left[
\exp\!\left(
\operatorname{clip}(f_{\mathrm{wait}}(x,\pi),b_{\min},b_{\max})
\right)-1
\right]
+\max(0,-96.717513+0.1440215I-0.1190117C)
+\hat t_{\mathrm{comm}}
}
$$

すなわち、

$$
\boxed{
\widehat{TTFT}
=\hat t_{\mathrm{wait}}
+\hat t_{\mathrm{compute}}
+\hat t_{\mathrm{comm}}
}
$$

である。学習済みモデルと評価結果は次に保存する。

- `models/unified_wait/unified_wait.joblib`
- `analysis/unified_wait/meta.json`
- `analysis/unified_wait/oof_predictions.csv`
- `analysis/unified_wait/model_comparison.csv`
- `analysis/unified_wait/scenario_metrics.csv`

### 3.5 同一条件での当てはまり

13,500 requests、15 independent scenariosについて、各test scenarioを丸ごと学習から外すleave-one-scenario-out評価を行った。通信項は旧式と同じく0として比較した。

| モデル | Wait MAE | TTFT MAE | TTFT誤差p50 | p90 | p95 | p99 | TTFT $R^2$ |
|---|---:|---:|---:|---:|---:|---:|---:|
| 旧component式 | 666.5 ms | **714.6 ms** | 49.2 ms | 1,079.7 ms | 4,090.6 ms | **13,598.9 ms** | **0.466** |
| 統合log-Ridge | 861.7 ms | 906.0 ms | 56.4 ms | 958.8 ms | 5,298.8 ms | 18,814.8 ms | 0.042 |
| **統合log-GBDT（Huber）** | **724.3 ms** | **766.3 ms** | **49.5 ms** | **863.1 ms** | **3,988.9 ms** | **15,979.3 ms** | **0.289** |
| 統合log-GBDT（squared error） | 758.2 ms | 803.9 ms | 49.9 ms | 871.9 ms | 4,348.8 ms | 16,987.7 ms | 0.221 |

統合Huberモデルは旧式に対してTTFT誤差p90を20.1%、p95を2.5%改善した。一方、全体MAEは7.2%、p99は17.5%悪化し、$R^2$も低下した。したがって、**統合によって式は簡潔になり通常域からp95までのrobustな当てはまりは改善したが、極端なroute tailの絶対時間を十分に回収できていない**。これは統合モデルを無条件に旧式より高精度とみなせないことを意味する。

v2では候補比較に使う中心式として統合Huberモデルを採用する。ただしp99 SLOやhard deadlineにはpoint predictionを使わず、別のquantile modelまたは上側risk項を加える。旧hurdle modelは比較baselineおよびroute発生の診断用として残すが、v2のTTFTスコアには含めない。

## 4. 候補GPU選択への展開

候補GPU $j$ ごとにcandidate-relativeな特徴量 $x_{ij}$ を作り、総コストを

$$
\widehat C_{ij}
=\widehat{TTFT}(x_{ij},\pi)
+\hat t_{\mathrm{request\ migration},ij}
+\hat t_{\mathrm{KV\ migration},ij}
+\hat t_{\mathrm{downlink},ij}
$$

として、

$$
j^*=\arg\min_j \widehat C_{ij}
$$

を選ぶのが基本形である。

一方、counterfactual実験では、初期の式単体はcapacity-pressureヒューリスティックより悪かった。主因は、compute項が候補間で共通、route項が低い発生確率によって消えやすい、旧scheduler項が0へclipされる、という3点だった。log1p schedulerへの更新で大きく改善したが、18 requestという小標本では勝敗が少数caseへ集中している。

式とcapacity pressureを組み合わせるguardrailも検討した。

$$
S_{ij}=\widehat C_{ij}+\lambda q_{\mathrm{cap},ij}
$$

新formulaでは平均regret 21.2 msまで改善したが、優位性は3 requestを除くと反転した。したがって、現段階で「学習式が常にpressureより優れる」とは結論できない。実用上はcapacity/admissionをhard guardrailとし、その内側でTTFT式をtie-breakまたはsoft scoreとして使うのが妥当である。

## 5. 予測誤差から得た重要な限界

### 5.1 Long-tail route時間

13,500 requests、15 scenariosのleave-one-scenario-out評価では、旧component式のTTFT MAEは714.6 ms、$R^2=0.466$だった。v2統合Huber式はTTFT MAE 766.3 ms、$R^2=0.289$である。両者ともabsolute errorのp50は約49 msである一方、p95は約4.0秒、p99は13.6–16.0秒であり、通常域とtailで性質が大きく異なる。v2はp90・p95を改善したがp99を悪化させており、極端なroute待ちの絶対値は依然として十分に予測できない。

したがって、point predictionをそのままp95/p99 SLOやhard deadlineの保証に使ってはならない。上側予測、quantile model、または誤選択コストを含む意思決定が必要である。

### 5.2 Snapshot staleness

候補選択時点の状態が正確でも、first tokenまでに別requestが同じGPUへ到着すると予測は外れる。counterfactual候補161件では、decisionからfirst tokenまでの新規到着数とabsolute errorの相関が0.803だった。

| 期間内の新規到着数 | 件数 | 平均absolute error |
|---:|---:|---:|
| 0 | 129 | 29.3 ms |
| 1 | 26 | 177.7 ms |
| 2 | 4 | 231.2 ms |
| 3 | 2 | 685.5 ms |

これは係数の再学習だけでは解消できない。候補別の将来到着riskを

$$
E[N_{ij}^{\mathrm{future}}]\approx\hat\lambda_j\,\widehat{TTFT}_{ij}
$$

のように加えるか、redirect実行直前に再評価する必要がある。

### 5.3 Counterfactual不足

通常実行では選ばれたGPUの実測TTFTしか得られず、選ばれなかった候補の真値は観測できない。絶対TTFTの予測精度が高くても候補順位が正しいとは限らないため、routing評価には候補ごとのcounterfactual replayが必要である。評価指標にはTop-1だけでなく

$$
regret_i
=TTFT_{i,j^*}-\min_j TTFT_{ij}
$$

を用いる。平均、中央値、p95 regretと順位相関を併記する。

## 6. PP度選択への展開

PP度 $p$ のTTFTは、構成比較に必要な項を明示すると

$$
\boxed{
TTFT_p
=W_{\mathrm{queue},p}
+T_{\mathrm{prefill},p}
+T_{\mathrm{comm},p}
+P_{\mathrm{redirect},p}C_{\mathrm{redirect},p}
}
$$

と書ける。redirect 1件の追加コストは

$$
C_{\mathrm{redirect},p}
=T_{\mathrm{route},p}
+T_{\mathrm{KV},p}
+T_{\mathrm{target\ queue},p}
$$

である。これは2章のrequest単位分解を、構成比較向けの期待値へまとめ直した式である。

### 6.1 PP1とPP2の損益分岐

PP2を選ぶ条件 $TTFT_2<TTFT_1$ は

$$
\boxed{
\left(W_{\mathrm{queue},1}-W_{\mathrm{queue},2}\right)
+\left(P_{\mathrm{redirect},1}C_{\mathrm{redirect},1}
-P_{\mathrm{redirect},2}C_{\mathrm{redirect},2}\right)
>
\left(T_{\mathrm{prefill},2}+T_{\mathrm{comm},2}\right)
-\left(T_{\mathrm{prefill},1}+T_{\mathrm{comm},1}\right)
}
$$

となる。言い換えると、

$$
\boxed{
\text{queue短縮}+\text{redirect削減}
>\text{PP実行時間増分}+\text{PP通信・同期コスト}
}
$$

がPP2の勝利条件である。

今回のPP2の主な利得は、単一requestの計算高速化ではない。モデル重量がstageへ分割され、GPU当たりのKV cacheとbatch収容余力が増えることで、PP1のqueueとcapacity redirectが減る点にある。一方で論理インスタンス数は $R_p=G/p$ へ減るため、低負荷・短promptではPP通信や同期の追加コストが勝ちやすい。

### 6.2 負荷と容量の境界

到着率を $\lambda$、論理インスタンス当たりの実効処理率を $\mu_p^{\mathrm{eff}}$ とすると、概略の負荷率は

$$
\rho_p=\frac{\lambda}{R_p\mu_p^{\mathrm{eff}}},
\qquad R_p=\frac{G}{p}
$$

である。queue待ちは例えば

$$
W_{\mathrm{queue},p}
\approx a_p\frac{\rho_p^{k_p}}{1-\rho_p}
$$

のように飽和付近で非線形に増える。ただし $\mu_p^{\mathrm{eff}}$ には計算速度だけでなくadmission可能率 $A_p$ を含める必要がある。

$$
\mu_p^{\mathrm{eff}}=\mu_p A_p(L,B,H,M_{\mathrm{free}})
$$

均等stage分割の近似では、admission可能条件は

$$
\frac{M_{\mathrm{model}}}{p}
+BL(1-H)m_{\mathrm{KV/token}}
+M_{\mathrm{activations}}(B)
\le M_{\mathrm{GPU}}
$$

となる。PP1では不成立、PP2では成立する領域がPP2の主要な勝ち筋である。ただし共有prefixのKVはrequestごとに単純加算できないため、最終判定にはシミュレータの実KV使用量またはblock単位の解析値を使う。

### 6.3 実測からの暫定結論

10 GPUのPP1×10とPP2×5を比較した実験では、8,000-token workloadで平均TTFTが20.4–42.8%、p95が35.5–64.2%改善し、redirectも大幅に減った。一方、2,000 tokens・reuse 25%では平均改善が0.3%に留まり、p95は17.8%悪化した。したがって「8,000 tokens」が普遍的な閾値なのではなく、PP1の容量圧力によってqueueとredirectが非線形に増え始める点が本質的な境界である。

また、完走比較ではTPOTとend-to-end latencyは悪化した。PP選択はTTFTだけでなく、decode latencyまたはthroughputとの多目的最適化として扱う必要がある。

## 7. 投機的KV転送の位置付け

Redirect判断まで実際の待機窓 $D_i$ が存在する場合、KV転送時間 $T_{\mathrm{KV},i}$ の一部を投機的に隠せる。予測先が最終選択先と一致する確率を $q_i$ とし、誤投機の資源コストを $C_{\mathrm{waste},i}$ とすると、概念上の期待利得は

$$
E[G_i]
\approx q_i\min(D_i,T_{\mathrm{KV},i})
-(1-q_i)C_{\mathrm{waste},i}
$$

である。現在の設計では誤投機が実容量をblockしないため $C_{\mathrm{waste}}$ は小さいが、多くのredirectは検知と決定が同一tickで起き、$D_i=0$ だった。この場合、予測精度が高くても隠せる時間はない。

したがって投機の成立条件は「redirectしそうか」だけではなく、

$$
\boxed{D_i>0\quad\text{かつ}\quad q_i\min(D_i,T_{\mathrm{KV},i})>C_{\mathrm{waste},i}}
$$

である。効果を増やすには、即決caseへ常時投機するより、候補capacity待ちなど構造的な待機窓が存在するcaseを対象にする必要がある。

## 8. 定式化の発展経緯

| 段階 | 定式化・検証 | 得られた知見 |
|---|---|---|
| 成分分析 | TTFTをroute、scheduler、compute、commへ分解 | workloadごとに支配項が異なり、long-tailではrouteが支配的 |
| 初期送信時式 | route hurdle + scheduler Ridge + compute線形和 | 通常領域は説明できるが、route tailを過小予測 |
| 安全側routing | route positive予測へOOF residual p90を加算 | deadline用途では期待値でなく上側予測が必要 |
| Candidate比較 | 候補ごとに同じ式を評価 | 絶対TTFT精度と候補ranking精度は別問題 |
| Counterfactual評価 | 未選択候補も強制実行しregretを計測 | 初期式はcapacity pressureより弱かった |
| Scheduler更新 | ms直接回帰+0 clipからlog1p/expm1へ変更 | 候補の縮退tieが減り、式単体rankingが改善 |
| v2 wait統合 | route発生確率と正値時間の積を廃止し、route+schedulerを単一log1p targetへ統合 | p90・p95誤差は改善したが、MAE・p99・$R^2$は旧component式より悪化 |
| Staleness分析 | decision後の新規到着を計測 | 将来到着が大外れの主要因で、snapshot特徴量だけでは解決不能 |
| PP損益分岐 | queue/redirect削減とPP追加コストを比較 | PPの利得はprompt長そのものより容量圧力の非線形境界で決まる |
| 投機転送 | 判断待機中にKV転送を前倒し | 予測精度より先に、隠蔽可能な待機窓の存在が必要 |

## 9. 現時点の統一的な意思決定原理

これまでの結果を統一すると、routing先またはPP度などのaction $a$ は、単純なpoint TTFT最小化ではなく、次のrisk-aware objectiveで選ぶのが自然である。

$$
\boxed{
a^*=\arg\min_{a\in\mathcal A_{\mathrm{admissible}}}
\left[
E(TTFT\mid x,a)
+\kappa Q_{\tau}(TTFT\mid x,a)
+\lambda_{\mathrm{cap}}R_{\mathrm{cap}}(x,a)
+\lambda_{\mathrm{stale}}R_{\mathrm{future\ arrival}}(x,a)
+C_{\mathrm{comm}}(x,a)
\right]
}
$$

ここで、

- $\mathcal A_{\mathrm{admissible}}$: KV、slot、token budgetを満たすaction集合
- $E(TTFT\mid x,a)$: v2の統合wait + compute + communication point prediction
- $Q_\tau$: p95などtail riskの予測
- $R_{\mathrm{cap}}$: capacity pressureやadmission failure risk
- $R_{\mathrm{future\ arrival}}$: snapshot後の新規到着によるstaleness risk
- $C_{\mathrm{comm}}$: KV migration、request migration、PP通信・同期コスト

である。

現在実装済みなのは主に期待TTFT、route上側予測、capacity snapshot、明示的migration costである。tail quantileとfuture-arrival riskは、今後の定式化で埋めるべき主要項である。

## 10. 次の定式化で優先すべきこと

1. **統合waitのtail改善**: p90・p95の改善を保ちながら、悪化したMAE・p99・$R^2$を改善する。mean用とquantile用の目的を分けて評価する。
2. **Tailを直接目的化**: mean TTFT、p95、p99、SLO violation probabilityを別々に推定する。
3. **Future-arrival riskの導入**: 候補別到着率と予測実行窓から、decision後の割込みriskを推定する。
4. **候補ranking向け学習**: absolute TTFTだけでなく、pairwise lossまたはregretを目的にする。ただしcounterfactual標本を増やす。
5. **PP境界の系統掃引**: input length、arrival rate、reuse、output length、burst、KV dtype、max_num_seqsを掃引し、容量圧力に対するhinge境界を推定する。
6. **不確実性を含む選択**: 点推定差が誤差幅より小さい場合はpressureなどのrobust heuristicへfallbackする。
7. **再評価機構**: redirect決定から実行までに状態が変わった場合、commit直前に候補を再確認する。

## 11. 参照資料

- `experiments/2026-07-16_ttft_component_regression/reports/ttft_formula.md`
- `experiments/2026-07-16_ttft_component_regression/reports/ttft_formula_worked_example.md`
- `experiments/2026-07-16_ttft_component_regression/reports/post_routing_component_dominance_v2.md`
- `experiments/2026-07-21-add_gpu_utilization/MODEL_ITERATION_HISTORY.md`
- `experiments/2026-07-21_mixed_workload_model_value/reports/final_three_policy_analysis.md`
- `experiments/2026-07-22_pp2_five_workloads/reports/interim_pp1_vs_pp2.md`
- `experiments/2026-07-22_pp2_five_workloads/reports/speculative_kv_transfer_verification.md`
- `experiments/2026-07-28_formulation_v2/pp2_ttft_break_even_analysis.md`
- `experiments/2026-07-28_pp_schedule_conceal_and_speculative/reports/branch_predictor_analogy_analysis.md`
