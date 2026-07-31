# KV移送あり・なしの損益分岐条件

## 1. 比較対象

本レポートではPP度$p$を固定し、Prefix KVを移送する場合と移送しない場合を比較する。PP1とPP2の比較は[`pp_break_even_report.md`](pp_break_even_report.md)で扱う。

「KV移送なし」には異なる二つの選択があるため、次の二つを分けて考える。

1. 同じredirect先で、KVを移送するかprefixを再計算するか。
2. Home instanceでcapacity解放を待つか、別instanceへKVを移送してredirectするか。

## 2. KV移送とprefix再計算の比較

PP度$p$とredirect先$j$を固定する。Routingとschedulerの状態は同じと仮定する。

### 2.1 KVを移送する場合

$C_{ij}$ token分のprefix KVを移送して再利用する場合、KV移送と計算の合計は

$$
\boxed{
T_{\mathrm{move}}
=t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
+\frac{L_i-C_{ij}}
{B_{\mathrm{com},p}}
}
$$

となる。

### 2.2 KVを移送しない場合

Redirect先でprefixを最初から再計算する場合は

$$
\boxed{
T_{\mathrm{recompute}}
=\frac{L_i}{B_{\mathrm{com},p}}
}
$$

となる。

## 3. 移送と再計算の損益分岐条件

KV移送が有利になる条件は

$$
T_{\mathrm{move}}<T_{\mathrm{recompute}}
$$

である。共通する$(L_i-C_{ij})/B_{\mathrm{com},p}$を整理すると、

$$
\boxed{
t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
<
\frac{C_{ij}}{B_{\mathrm{com},p}}
}
$$

となる。左辺はKV移送時間、右辺はprefix $C_{ij}$ tokenの再計算時間である。

したがって損益分岐点は

$$
\boxed{
\text{KV移送時間}
=\text{prefix再計算時間}
}
$$

である。

## 4. Prefix長の境界

KVを1 token分移送する時間を

$$
a_p^{\mathrm{KV}}
=\frac{8m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6B_{h_i j}}
$$

と置く。KV移送が長いprefixに対して有利になるには、まず

$$
\boxed{
a_p^{\mathrm{KV}}
<\frac{1}{B_{\mathrm{com},p}}
}
$$

が必要である。つまり、1 token当たりの移送時間が1 token当たりの再計算時間より短くなければならない。

この条件を満たす場合、prefix長の損益分岐点は

$$
\boxed{
C_p^*
=\frac{t_0^{\mathrm{KV}}}
{\dfrac{1}{B_{\mathrm{com},p}}-a_p^{\mathrm{KV}}}
}
$$

である。よって、

$$
\boxed{
\begin{cases}
C_{ij}>C_p^* &: \text{KV移送が有利},\\
C_{ij}<C_p^* &: \text{再計算が有利}
\end{cases}
}
$$

となる。短いprefixでは固定移送時間$t_0^{\mathrm{KV}}$を回収できないため、再計算が有利になりやすい。

## 5. ネットワーク帯域の境界

同じ条件を帯域$B_{h_i j}$について解くと、KV移送に必要な最低帯域は

$$
\boxed{
B_p^*(C_{ij})
=\frac{8C_{ij}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6
\left(
\dfrac{C_{ij}}{B_{\mathrm{com},p}}
-t_0^{\mathrm{KV}}
\right)}
}
$$

となる。ただし、$C_{ij}/B_{\mathrm{com},p}>t_0^{\mathrm{KV}}$が必要である。

$$
\boxed{
\begin{cases}
B_{h_i j}>B_p^*(C_{ij}) &: \text{KV移送が有利},\\
B_{h_i j}<B_p^*(C_{ij}) &: \text{再計算が有利}
\end{cases}
}
$$

## 6. Home待機とKV付きredirectの比較

移送なしを「home instanceに留まる」という意味で使う場合は、前節とは異なる比較になる。

Home instanceで処理する時間を

$$
T_{\mathrm{home}}
=t_{\mathrm{route},i,h_i,p}
+t_{\mathrm{sched},i,h_i,p}
+t_{\mathrm{com},i,h_i,p}
$$

とする。別instance $r$へKV付きでredirectする時間を

$$
T_{\mathrm{redirect}}
=t_{\mathrm{route},i,r,p}
+t_{\mathrm{sched},i,r,p}
+t_{\mathrm{com},i,r,p}
+t_{\mathrm{KVtransfer},i,r,p}
$$

とする。この場合、redirectが有利になる条件は

$$
\boxed{
\begin{aligned}
&\left(t_{\mathrm{route},i,h_i,p}-t_{\mathrm{route},i,r,p}\right)
+\left(t_{\mathrm{sched},i,h_i,p}-t_{\mathrm{sched},i,r,p}\right)\\
&\quad+
\left(t_{\mathrm{com},i,h_i,p}-t_{\mathrm{com},i,r,p}\right)
>
t_{\mathrm{KVtransfer},i,r,p}
\end{aligned}
}
$$

である。

したがって、home待機とKV付きredirectの損益分岐点は

$$
\boxed{
\text{redirectによるrouting・scheduler・計算時間の削減}
=\text{KV移送時間}
}
$$

となる。

## 7. Home待機に対する必要最低帯域

Redirectによって削減できる時間を

$$
\begin{aligned}
H_{i,r,p}
={}&t_{\mathrm{route},i,h_i,p}-t_{\mathrm{route},i,r,p}\\
&+t_{\mathrm{sched},i,h_i,p}-t_{\mathrm{sched},i,r,p}\\
&+t_{\mathrm{com},i,h_i,p}-t_{\mathrm{com},i,r,p}
\end{aligned}
$$

と置く。$H_{i,r,p}>t_0^{\mathrm{KV}}$の場合、KV付きredirectに必要な最低帯域は

$$
\boxed{
B_{h_i r}^{*}
=\frac{8C_{ir}m_{\mathrm{KV/token}}}
{p\eta_{\mathrm{KV}}10^6
\left(H_{i,r,p}-t_0^{\mathrm{KV}}\right)}
}
$$

である。

$$
\boxed{
\begin{cases}
B_{h_i r}>B_{h_i r}^{*} &: \text{KV付きredirectが有利},\\
B_{h_i r}<B_{h_i r}^{*} &: \text{home待機が有利}
\end{cases}
}
$$

## 8. 考察

KV移送有無の判断では、何を「移送なし」とするかを区別する必要がある。

- 同じredirect先で再計算する場合は、KV移送時間とprefix再計算時間を比較する。
- Home instanceに留まる場合は、KV移送時間とredirectによって削減できるqueue・計算時間を比較する。

KV移送が有利になりやすいのは、次の条件である。

1. Prefix $C_{ij}$が十分に長い。
2. ネットワークの実効帯域$\eta_{\mathrm{KV}}B_{h_i j}$が大きい。
3. Home instanceのcapacity解放待ちまたはscheduler待ちが長い。
4. Redirect先のqueueが短い。
5. KV移送の固定時間$t_0^{\mathrm{KV}}$が小さい。

反対に、短いprefix、低帯域、home側のqueueが短い条件では、再計算またはhome待機が有利になりやすい。

最も単純な境界条件は、

$$
\boxed{
\text{KV移送 vs 再計算}:
\quad
t_{\mathrm{KVtransfer}}
=\frac{C_{ij}}{B_{\mathrm{com},p}}
}
$$

および

$$
\boxed{
\text{KV付きredirect vs home待機}:
\quad
t_{\mathrm{KVtransfer}}
=H_{i,r,p}
}
$$

である。
