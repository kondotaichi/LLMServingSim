# PP1とPP2の損益分岐条件

## 1. 比較対象

本レポートではKV移送方針を固定し、PP度だけを$p=1$から$p=2$へ変更したときのTTFTを比較する。KV移送あり・なしの選択そのものは[`kvtransfer_break_even_report.md`](kvtransfer_break_even_report.md)で扱う。

PP度$p$におけるTTFTを

$$
TTFT_{i,p}
=t_{\mathrm{route},i,p}
+t_{\mathrm{sched},i,p}
+t_{\mathrm{com},i,p}
+t_{\mathrm{KVtransfer},i,p}
$$

とする。

## 2. PP1とPP2の差分

PP2からPP1を引いたTTFT差を

$$
\boxed{
\Delta_i^{\mathrm{PP}}
=TTFT_{i,2}-TTFT_{i,1}
}
$$

と定義する。4成分へ展開すると、

$$
\boxed{
\begin{aligned}
\Delta_i^{\mathrm{PP}}
={}&
\left(t_{\mathrm{route},i,2}-t_{\mathrm{route},i,1}\right)\\
&+\left(t_{\mathrm{sched},i,2}-t_{\mathrm{sched},i,1}\right)\\
&+\left(t_{\mathrm{com},i,2}-t_{\mathrm{com},i,1}\right)\\
&+\left(t_{\mathrm{KVtransfer},i,2}-t_{\mathrm{KVtransfer},i,1}\right)
\end{aligned}
}
$$

である。

## 3. 損益分岐条件

PP2が有利となる条件は

$$
\boxed{
\Delta_i^{\mathrm{PP}}<0
}
$$

である。削減量と追加コストに分けて書けば、

$$
\boxed{
\begin{aligned}
&\underbrace{
t_{\mathrm{route},i,1}-t_{\mathrm{route},i,2}
}_{\text{routing待ちの削減}}
+\underbrace{
t_{\mathrm{sched},i,1}-t_{\mathrm{sched},i,2}
}_{\text{scheduler待ちの削減}}
+\underbrace{
t_{\mathrm{KVtransfer},i,1}-t_{\mathrm{KVtransfer},i,2}
}_{\text{KV転送時間の削減}}
\\
&\qquad>
\underbrace{
t_{\mathrm{com},i,2}-t_{\mathrm{com},i,1}
}_{\text{PP2の計算時間増分}}
\end{aligned}
}
$$

となる。したがって、損益分岐点そのものは

$$
\boxed{
\begin{aligned}
&t_{\mathrm{route},i,1}-t_{\mathrm{route},i,2}
+t_{\mathrm{sched},i,1}-t_{\mathrm{sched},i,2}
+t_{\mathrm{KVtransfer},i,1}-t_{\mathrm{KVtransfer},i,2}
\\
&\qquad=
t_{\mathrm{com},i,2}-t_{\mathrm{com},i,1}
\end{aligned}
}
$$

である。PP stage間通信を$t_{\mathrm{other}}$へ分離する場合は、右辺へ$t_{\mathrm{other},i,2}-t_{\mathrm{other},i,1}$を加える。

## 4. 元の定式化を代入した境界

Routing待ちを

$$
A_{i,p}
=\min_{j\in\mathcal J_i}
\frac{D_{i,j,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,p}}
$$

、scheduler待ちを

$$
S_{i,p}
=\frac{W_{\mathrm{ahead},i,j_{i,p}^*}}
{B_{\mathrm{schedfree},j_{i,p}^*,p}}
$$

、計算時間を

$$
P_{i,p}
=t_{0,p}^{\mathrm{com}}
+\frac{L_i-C_{i,j_{i,p}^*}}
{B_{\mathrm{com},p}}
$$

、KV転送時間を$K_{i,p}$と略記する。このときPPの損益分岐条件は

$$
\boxed{
A_{i,1}-A_{i,2}
+S_{i,1}-S_{i,2}
+K_{i,1}-K_{i,2}
=P_{i,2}-P_{i,1}
}
$$

となる。

## 5. KV容量に関する境界

PPによってlayerを均等分割すると、

$$
M_{i,2}^{\mathrm{req}}
=\frac{1}{2}M_{i,1}^{\mathrm{req}}
$$

である。PP1では容量不足だがPP2では即時admissionできる領域は、

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

である。この範囲では、PP2によってPP1のrouting待ち

$$
\frac{D_{i,j,1}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,1}}
$$

を削減できる可能性が高い。

## 6. Scheduler安定性に関する境界

総GPU数を$G$、system全体のarrival rateを$\lambda$とすると、1 logical instance当たりのarrival rateは

$$
\lambda_{j,p}\approx\frac{\lambda p}{G}
$$

である。PP2ではlogical instance数が半分になるため、1 instance当たりのarrival rateはPP1の2倍になる。

PP2のscheduler backlogが平均的に減少する条件は

$$
B_{\mathrm{com},2}
-\frac{2\lambda}{G}\overline U_2
>0
$$

である。したがって、PP2のscheduler安定境界は

$$
\boxed{
\lambda_{\mathrm{sat},2}
=\frac{GB_{\mathrm{com},2}}
{2\overline U_2}
}
$$

となる。$\lambda\geq\lambda_{\mathrm{sat},2}$では、容量面でPP2が有利でもscheduler queueを解消できない。

## 7. 考察

PP2には相反する二つの効果がある。

- GPU当たりのKV需要が半分になり、model weight分割によってKV budgetも増える。
- Logical instance数が半分になり、instance当たりarrival rateが2倍になる。

したがって、PP2が有利なのは主として次の領域である。

1. PP1ではKV容量不足によるrouting待ちまたはredirectが発生する。
2. PP2では容量不足が解消または緩和される。
3. PP2のarrival rateがscheduler安定境界$\lambda_{\mathrm{sat},2}$より小さい。
4. 待ち時間とKV転送時間の削減量が、PP2の計算・通信増分を上回る。

低負荷でPP1にも十分なKV余裕がある場合、PP2が削減できる待ち時間は小さい。この場合はPP1が有利になりやすい。反対にarrival rateが非常に高い場合も、PP2のlogical instance数減少によってscheduler queueが増え、PP1が再び有利になる可能性がある。

したがってPPの境界は、単純なinput token数ではなく、

$$
\boxed{
\text{PP2によるqueue・転送時間削減}
=\text{PP2による計算・通信時間増分}
}
$$

として表すのが適切である。
