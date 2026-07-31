# PP1・PP2およびKV転送有無の損益分岐

> Note: 境界条件を比較軸ごとに分けた版は、[`pp_break_even_report.md`](pp_break_even_report.md)と[`kvtransfer_break_even_report.md`](kvtransfer_break_even_report.md)を参照する。本ファイルは両者の相互作用を含む統合版として残す。

## 1. 目的

本レポートでは、[`ttft_formulation.md`](ttft_formulation.md)の4成分式を用いて、次の二つの選択条件を導く。

1. 同じGPU数$G$をPP1またはPP2で構成するとき、どちらのTTFTが短いか。
2. Prefix KVを別instanceへ転送するとき、転送、再計算、home instanceでの待機のどれが短いか。

ここで求める損益分岐点は、特定環境に固定された入力token数ではない。KV容量、到着率、計算throughput、ネットワーク帯域などによって移動する境界である。

## 2. 比較に用いるTTFT

PP度$p$において選択されるinstanceを$j_{i,p}^*$とし、記述を簡潔にするため、各成分を次のように置く。

$$
A_{i,p}
=\min_{j\in\mathcal J_i}
\frac{D_{i,j,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,p}}
$$

$$
S_{i,p}
=\frac{W_{\mathrm{ahead},i,j_{i,p}^*}}
{B_{\mathrm{schedfree},j_{i,p}^*,p}}
$$

$$
P_{i,p}
=t_{0,p}^{\mathrm{com}}
+\frac{L_i-C_{i,j_{i,p}^*}}
{B_{\mathrm{com},p}}
$$

$$
K_{i,p}
=I_{i,p}^{\mathrm{move}}
\left(
t_0^{\mathrm{KV}}
+\frac{8C_{i,j_{i,p}^*}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i,j_{i,p}^*}}
\right)
$$

ここで、

$$
I_{i,p}^{\mathrm{move}}
=\mathbf{1}[j_{i,p}^*\neq h_i]I_{\mathrm{KVmove},i}
$$

である。Routingとschedulerの固定時間を$t_0^{\mathrm{queue}}=t_0^{\mathrm{route}}+t_0^{\mathrm{sched}}$とまとめると、

$$
\boxed{
TTFT_{i,p}
\approx
t_0^{\mathrm{queue}}
+A_{i,p}
+S_{i,p}
+P_{i,p}
+K_{i,p}
}
$$

となる。

## 3. PP1とPP2の差

PP2からPP1を引いた差を

$$
\Delta_i^{2-1}=TTFT_{i,2}-TTFT_{i,1}
$$

と定義する。固定queue時間がPP度に依存しないと仮定すれば、

$$
\boxed{
\Delta_i^{2-1}
=
\underbrace{(A_{i,2}-A_{i,1})}_{\Delta_{\mathrm{route}}}
+\underbrace{(S_{i,2}-S_{i,1})}_{\Delta_{\mathrm{sched}}}
+\underbrace{(P_{i,2}-P_{i,1})}_{\Delta_{\mathrm{com}}}
+\underbrace{(K_{i,2}-K_{i,1})}_{\Delta_{\mathrm{KV}}}
}
$$

である。したがって、PP2が有利になる必要十分条件は

$$
\boxed{
TTFT_{i,2}<TTFT_{i,1}
\iff
(A_{i,1}-A_{i,2})
+(S_{i,1}-S_{i,2})
+(K_{i,1}-K_{i,2})
>
P_{i,2}-P_{i,1}
}
$$

である。左辺はPP2によるrouting待ち、scheduler待ち、KV転送時間の削減量、右辺はPP2による計算時間の増分である。PP stage間通信を$t_{\mathrm{other}}$として明示する場合は、そのPP2とPP1の差を右辺へ追加する。

## 4. PP2がKV容量へ与える効果

1 token当たりのGPU別KV容量は

$$
m_{\mathrm{KV/token},2}
=\frac{1}{2}m_{\mathrm{KV/token},1}
$$

である。したがって、同じrequestについて

$$
\boxed{
M_{i,2}^{\mathrm{req}}
=\frac{1}{2}M_{i,1}^{\mathrm{req}}
}
$$

となる。また、GPUメモリ$M^{\mathrm{GPU}}$からmodel weightとその他の予約領域を引いたKV budgetを

$$
M_p^{\mathrm{KV}}
=M^{\mathrm{GPU}}
-\frac{M^{\mathrm{weight}}}{p}
-M_p^{\mathrm{other}}
$$

と近似すると、weightが均等分割される限り、一般に$M_2^{\mathrm{KV}}>M_1^{\mathrm{KV}}$となる。

PP1では即時admissionできず、PP2ではできる容量境界は

$$
\boxed{
\begin{aligned}
M_{j,1}^{\mathrm{active}}+M_{i,1}^{\mathrm{req}}
&>M_1^{\mathrm{KV}},\\
M_{j,2}^{\mathrm{active}}+\frac{1}{2}M_{i,1}^{\mathrm{req}}
&\le M_2^{\mathrm{KV}}
\end{aligned}
}
$$

である。この領域では$D_{i,j,1}^{\mathrm{KV}}>0$かつ$D_{i,j,2}^{\mathrm{KV}}=0$となり、PP2はPP1で発生する容量解放待ちを除去できる。

同じ長さのrequestを$n_p$件収容する単純な場合、1 request当たりのKV需要を$M_{i,p}^{\mathrm{req}}$とすると、1 logical instanceのKV収容数は

$$
\boxed{
n_p^{\max}
=\left\lfloor
\frac{M_p^{\mathrm{KV}}}
{M_{i,p}^{\mathrm{req}}}
\right\rfloor
}
$$

であり、連続近似では

$$
\frac{n_2^{\max}}{n_1^{\max}}
\approx
2\frac{M_2^{\mathrm{KV}}}{M_1^{\mathrm{KV}}}
$$

となる。ただし、PP2ではlogical instance数が$G$から$G/2$へ半減する。このためsystem全体の収容数の比は概ね

$$
\boxed{
\frac{(G/2)n_2^{\max}}{Gn_1^{\max}}
\approx
\frac{M_2^{\mathrm{KV}}}{M_1^{\mathrm{KV}}}
}
$$

である。単にKVが2分割されることだけではsystem全体の収容数は2倍にならず、weight分割によって増えたKV budgetが正味の容量利得となる。

## 5. PP2がscheduler負荷へ与える効果

総arrival rateを$\lambda$、総GPU数を$G$とすると、均等分散時の1 instance当たりarrival rateは

$$
\lambda_{j,p}\approx\frac{\lambda p}{G}
$$

である。したがって、

$$
\lambda_{j,2}=2\lambda_{j,1}
$$

となる。PP2のscheduler backlogが平均的に減少する条件は

$$
B_{\mathrm{schedfree},2}
=B_{\mathrm{com},2}
-\frac{2\lambda}{G}\overline U_2
>0
$$

である。すなわち、PP2の安定条件は

$$
\boxed{
\lambda
<\lambda_{\mathrm{sat},2}
=\frac{GB_{\mathrm{com},2}}
{2\overline U_2}
}
$$

となる。一方、PP1では

$$
\boxed{
\lambda
<\lambda_{\mathrm{sat},1}
=\frac{GB_{\mathrm{com},1}}
{\overline U_1}
}
$$

である。$B_{\mathrm{com},2}<2B_{\mathrm{com},1}$かつ$\overline U_1\approx\overline U_2$なら、PP2の方が先にscheduler飽和へ近づく。このため、PP2は容量不足を緩和する一方で、logical instance数の減少によりscheduler queueを増やす可能性がある。

PP2が有利なのは、容量拡大による$A_{i,1}-A_{i,2}$の削減が大きく、かつarrival rateが$\lambda_{\mathrm{sat},2}$から十分離れている領域である。

## 6. 同じredirect先でKVを転送するか再計算するか

次にPP度$p$とredirect先$j$を固定し、prefix KVを転送する場合と、転送せずprefixを再計算する場合を比較する。

KVを転送する場合は、$C_{ij}$ tokenを再利用するため

$$
T_{\mathrm{move}}
=t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
+\frac{L_i-C_{ij}}{B_{\mathrm{com},p}}
$$

である。転送せず再計算する場合は

$$
T_{\mathrm{recompute}}
=\frac{L_i}{B_{\mathrm{com},p}}
$$

である。両者でroutingとschedulerの状態が同じなら、KV転送が有利となる条件は

$$
\boxed{
t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
<
\frac{C_{ij}}{B_{\mathrm{com},p}}
}
$$

である。左辺がKV転送時間、右辺がprefixを再計算する時間である。

1 token当たりの転送時間を

$$
a_p^{\mathrm{KV}}
=\frac{8m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
$$

と置くと、

$$
t_0^{\mathrm{KV}}+a_p^{\mathrm{KV}}C_{ij}
<\frac{C_{ij}}{B_{\mathrm{com},p}}
$$

である。したがって、まず

$$
\boxed{
\frac{1}{B_{\mathrm{com},p}}>a_p^{\mathrm{KV}}
}
$$

が必要である。転送の1 token当たり時間が再計算より長い場合、prefixがどれだけ長くても転送は有利にならない。

この条件を満たす場合のprefix長の損益分岐点は

$$
\boxed{
C_{p}^{*}
=\frac{t_0^{\mathrm{KV}}}
{\dfrac{1}{B_{\mathrm{com},p}}-a_p^{\mathrm{KV}}}
}
$$

であり、

$$
C_{ij}>C_p^*
$$

ならKV転送、$C_{ij}<C_p^*$なら再計算が有利となる。

同じ式をネットワーク帯域について解くと、必要最低帯域は

$$
\boxed{
B_{h_i j}
>
B_p^*(C_{ij})
=\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6
\left(\dfrac{C_{ij}}{B_{\mathrm{com},p}}-t_0^{\mathrm{KV}}\right)}
}
$$

となる。ただし$C_{ij}/B_{\mathrm{com},p}>t_0^{\mathrm{KV}}$が必要である。

## 7. Homeで待つか、KVを転送してredirectするか

Home instanceを$h_i$、redirect候補を$r$とする。比較に共通する固定時間を除き、homeに留まるコストを

$$
T_{\mathrm{home}}
=A_{i,h_i,p}+S_{i,h_i,p}+P_{i,h_i,p}
$$

とし、redirectするコストを

$$
T_{\mathrm{redirect}}
=A_{i,r,p}+S_{i,r,p}+P_{i,r,p}+K_{i,r,p}
$$

とする。KV付きredirectが有利となる条件は

$$
\boxed{
\underbrace{A_{i,h_i,p}-A_{i,r,p}}_{\text{routing待ちの削減}}
+\underbrace{S_{i,h_i,p}-S_{i,r,p}}_{\text{scheduler待ちの削減}}
+\underbrace{P_{i,h_i,p}-P_{i,r,p}}_{\text{計算時間の削減}}
>
\underbrace{K_{i,r,p}}_{\text{KV転送コスト}}
}
$$

である。つまり、redirectによって削減できる待ち時間と計算時間の合計がKV転送時間を上回ることが損益分岐条件となる。

削減可能時間を

$$
H_{i,r,p}
=(A_{i,h_i,p}-A_{i,r,p})
+(S_{i,h_i,p}-S_{i,r,p})
+(P_{i,h_i,p}-P_{i,r,p})
$$

と置けば、必要最低帯域は

$$
\boxed{
B_{h_i r}
>
\frac{8C_{ir}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6
(H_{i,r,p}-t_0^{\mathrm{KV}})}
}
$$

となる。ただし$H_{i,r,p}>t_0^{\mathrm{KV}}$が必要である。Home側の待ちが小さければredirectは転送コストを回収できず、容量解放待ちが長いほどredirectが有利になる。

## 8. PP度とKV転送の相互作用

PP2ではGPU当たりのKV data量が半分になるため、同じprefixを同じ帯域で移送するとき、可変部分は

$$
\boxed{
K_{i,2}^{\mathrm{variable}}
=\frac{1}{2}K_{i,1}^{\mathrm{variable}}
}
$$

となる。ただし$t_0^{\mathrm{KV}}$は半分にならない。また、PP2が容量不足を解消してredirect自体を不要にする場合は$K_{i,2}=0$となる。

一方、PP2では複数stageへのKV転送が並列ではなく直列化される、またはstageごとに固定遅延が発生する実装も考えられる。その場合は、$t_0^{\mathrm{KV}}$をstage数に依存させ、実測したaggregate bandwidthを用いる必要がある。

4通りの選択肢は次のように整理できる。

| 構成 | 主な利得 | 主なコスト |
|---|---|---|
| PP1・転送なし | Instance数が多い、PP通信なし | GPU当たりKV需要とweight占有が大きい |
| PP1・転送あり | 空いているinstanceとprefix KVを利用可能 | KV転送量が大きい |
| PP2・転送なし | GPU当たりKV需要が半分、KV budgetが増加 | Instance数が半減、PP計算・通信コスト |
| PP2・転送あり | 容量利得に加えてGPU当たり転送量が半分 | Instance数半減、固定転送・PPコスト |

最終的には

$$
\boxed{
(p^*,x^*)
=\mathop{\arg\min}_{p\in\{1,2\},\,x\in\{0,1\}}
TTFT_{i,p,x}
}
$$

として選べる。$x=1$はKV転送、$x=0$は転送なしを表す。

## 9. 損益分岐点に関する考察

### 9.1 低負荷・容量余裕領域

$D_{i,j,1}^{\mathrm{KV}}=D_{i,j,2}^{\mathrm{KV}}=0$かつscheduler queueも小さい場合、PP2が削減できる待ち時間はほとんどない。この領域ではPP2の計算・通信増分を回収できないため、通常はPP1・転送なしが有利である。

### 9.2 PP1だけが容量不足になる領域

PP1で$D_{i,j,1}^{\mathrm{KV}}>0$、PP2で$D_{i,j,2}^{\mathrm{KV}}=0$になると、PP2は

$$
\frac{D_{i,j,1}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,1}}
$$

に相当する待ちを削減できる。これがPP2の追加計算・通信時間を超えた点が、容量圧力に関するPP1/PP2の損益分岐点となる。

### 9.3 高arrival rate領域

PP2はlogical instance数が半分なので、$\lambda$が$\lambda_{\mathrm{sat},2}$へ近づくとscheduler待ちが急増する。この領域では、KV容量に余裕があってもPP1が再び有利になる可能性がある。したがってPP2の勝ち領域は、単純な「inputが長いほどPP2」という単調な領域ではなく、容量圧力が高い一方でPP2のschedulerが飽和していない中間領域になり得る。

### 9.4 Prefix長とネットワーク帯域

KV転送には固定時間があるため、短いprefixでは再計算が有利になりやすい。Prefixが長くなると再計算時間も線形に増えるため、転送の1 token当たり時間が再計算より短ければ、$C_p^*$を超えたところでKV転送が有利になる。帯域が低い、または$\eta_{\mathrm{KV}}$が小さい場合は$C_p^*$が大きくなる。

### 9.5 Routingの損益分岐

転送するかどうかは、転送時間だけで決められない。Homeの容量解放待ち、redirect先のrouting待ち、両者のscheduler queue、prefix再利用後の計算時間を合わせて比較する必要がある。特にhomeでのrouter queueが長い場合、転送自体が再計算より多少遅くても、redirectによる待ち時間削減を含めれば転送が有利になり得る。

## 10. 定式化から直接求められる境界

| 判断 | 損益分岐条件 |
|---|---|
| PP2 vs PP1 | $A_1-A_2+S_1-S_2+K_1-K_2=P_2-P_1$ |
| PP2のcapacity利得 | $D_1>0$かつ$D_2=0$ |
| PP2のscheduler安定性 | $\lambda<GB_{\mathrm{com},2}/(2\overline U_2)$ |
| KV転送 vs 再計算 | $t_0^{\mathrm{KV}}+a_p^{\mathrm{KV}}C=C/B_{\mathrm{com},p}$ |
| KV転送のprefix境界 | $C=C_p^*$ |
| Redirect vs home待機 | $H_{i,r,p}=K_{i,r,p}$ |

## 11. 数値的な損益分岐点を求めるために必要な値

式の形から判断条件は導けるが、具体的なtoken数、arrival rate、帯域の境界を求めるには次の値を実測する必要がある。

- $M_1^{\mathrm{KV}}$、$M_2^{\mathrm{KV}}$: PPごとのGPU当たりKV budget
- $M_{j,p}^{\mathrm{active}}$: request到着時のactive KV予約量
- $B_{\mathrm{KVfree},j,p}$: 状態ごとの実効KV解放速度
- $W_{\mathrm{ahead},i,j}$: scheduler内の先行残存token workload
- $B_{\mathrm{com},1}$、$B_{\mathrm{com},2}$: PPごとの実効prefill throughput
- $t_{0,1}^{\mathrm{com}}$、$t_{0,2}^{\mathrm{com}}$: PPごとの固定計算時間
- $t_0^{\mathrm{KV}}$、$B_{h_i j}$、$\eta_{\mathrm{KV}}$: KV転送の固定時間と実効帯域
- PP stage間通信を4成分外へ分離する場合の$t_{\mathrm{other},p}$

特に$B_{\mathrm{KVfree}}$と$W_{\mathrm{ahead}}$は現在の単純fittingでは代理変数であり、数値境界を高精度に求めるには直接ログへ記録する必要がある。

## 12. 結論

PP2の本質的な利得は、GPU当たりのKV需要を半分にし、weight分割によってKV budgetを増やすことで、routing待ちやredirectを減らせる点にある。一方、logical instance数が半分になるため、instance当たりarrival rateは2倍となり、scheduler飽和は早まる可能性がある。

したがってPP2の損益分岐は、

$$
\boxed{
\text{routing・scheduler・KV転送の削減}
=\text{PP2の計算・通信増分}
}
$$

で与えられる。

KV転送については、同じredirect先で比較する場合、

$$
\boxed{
\text{KV転送時間}
=\text{prefix再計算時間}
}
$$

が転送と再計算の境界である。Home待機との比較では、

$$
\boxed{
\text{redirectによる待ち・計算時間の削減}
=\text{KV転送時間}
}
$$

が境界となる。これらを個別のtoken長だけでなく、capacity pressure、arrival rate、実効throughput、実効帯域によって評価する必要がある。
