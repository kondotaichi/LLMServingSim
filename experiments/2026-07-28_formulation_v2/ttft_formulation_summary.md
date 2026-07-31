# TTFTの依存関係に基づく定式化

## 1. 目的

本書では、TTFTを高精度に回帰することではなく、TTFTがどの物理量・負荷量・容量制約に依存するかを明示する。式中の係数はモデル、GPU、parallelism、実装によって変わるため、環境ごとに実測値から推定する。

## 2. 記号

| 記号 | 意味 |
|---|---|
| $L_i$ | request $i$のinput token数 |
| $O_i$ | request $i$の予定output token数 |
| $C_{ij}$ | candidate $j$で再利用できるprefix token数 |
| $U_{ij}=L_i-C_{ij}$ | prefillで新たに計算するtoken数 |
| $\lambda_j$ | candidate $j$へのrequest到着率 |
| $N_j^{\mathrm{wait}},N_j^{\mathrm{run}}$ | waiting、running request数 |
| $M_j^{\mathrm{active}}$ | active requestsが必要とするKV容量 |
| $M_p^{\mathrm{KV}}$ | PP度$p$におけるGPU当たりのKV budget |
| $B$ | 通信帯域 |
| $B_{\mathrm{batch}}$ | batch内の総token数 |
| $p$ | pipeline parallel degree |
| $G$ | 総GPU数 |

## 3. TTFTの基本式

Request $i$をcandidate $j$、PP度$p$で処理するとき、

$$
\boxed{
TTFT_{i,j,p}
=T_{\mathrm{route},i,j,p}
+T_{\mathrm{queue},i,j,p}
+T_{\mathrm{prefill},i,j,p}
+T_{\mathrm{comm},i,j,p}
}
$$

と分解する。旧式のようにrouter待ちを発生確率と発生時時間へ分け、$p_{\mathrm{route}}T_{\mathrm{route},+}$とはしない。routingとschedulerはrequestから見ると連続した待ち時間であり、各時間をそのまま加算する。

## 4. Prefill計算時間

Prefix cacheを除いた未計算token数は

$$
\boxed{U_{ij}=L_i-C_{ij}}
$$

である。Prefill時間は未計算token数、batch token数、PP度に依存する。

$$
\boxed{
T_{\mathrm{prefill},i,j,p}
=t_{0,p}^{\mathrm{pre}}
+k_p^{\mathrm{pre}}(B_{\mathrm{batch}})U_{ij}
}
$$

- $t_{0,p}^{\mathrm{pre}}$: kernel launchやpipeline fillを含む固定時間
- $k_p^{\mathrm{pre}}$: 1 token当たりの実効計算時間

非線形な領域では、$k_p^{\mathrm{pre}}(B_{\mathrm{batch}})$をprofileから区分線形に与える。

## 5. KV cache容量

1 token当たりのKV cacheサイズは

$$
\boxed{
m_{\mathrm{KV/token},p}
=\frac{2N_{\mathrm{layer}}}{p}
N_{\mathrm{KVhead}}d_{\mathrm{head}}b_{\mathrm{KV}}
}
$$

で近似する。係数2はKeyとValueを表す。KV block sizeを$q$ tokensとすると、request $i$が完了までに必要とするGPU当たりのKV容量は

$$
\boxed{
M_{i,p}^{\mathrm{req}}
=q\left\lceil\frac{L_i+O_i}{q}\right\rceil
m_{\mathrm{KV/token},p}
}
$$

となる。Candidate $j$へrequest $i$を追加したときのcapacity pressureを

$$
\boxed{
\phi_{i,j,p}
=\frac{M_{j,p}^{\mathrm{active}}+M_{i,p}^{\mathrm{req}}}
{M_p^{\mathrm{KV}}}
}
$$

と定義する。

## 6. Admission条件

Requestを即座にadmitできる条件は、KV容量、sequence slot、token budgetを満たすことである。

$$
\boxed{
A_{i,j,p}
=\mathbf{1}\left[
\phi_{i,j,p}\le1
\land N_j^{\mathrm{run}}+1\le N_p^{\max}
\land U_{ij}\le B_p^{\mathrm{token}}
\right]
}
$$

$A_{i,j,p}=0$なら、capacity解放を待つか、別candidateへredirectする必要がある。Chunked prefillを使う場合、token budget条件は1 iterationで処理できる量を表す。

## 7. Queue待ち時間

総GPU数$G$に対する論理instance数と負荷率は

$$
\boxed{R_p=\frac{G}{p}}
$$

$$
\boxed{
\rho_p=\frac{\lambda}{R_p\mu_p^{\mathrm{eff}}}
}
$$

である。実効処理率は計算速度だけでなく、admission可能率にも依存する。

$$
\boxed{
\mu_p^{\mathrm{eff}}
=\mu_p\,\overline{A}_p(L,O,C,M^{\mathrm{active}},N^{\mathrm{run}})
}
$$

ここで$\overline{A}_p=P(A_{i,j,p}=1)$は、到着requestを即時admitできる割合である。

Queue待ちは負荷率が1へ近づくと非線形に増加する。

$$
\boxed{
T_{\mathrm{queue},i,j,p}
\approx
a_p\frac{\rho_{j,p}^{k_p}}{1-\rho_{j,p}}
g\!\left(N_j^{\mathrm{wait}},N_j^{\mathrm{run}},\phi_{i,j,p}\right)
}
$$

- $a_p$: scheduler iteration等の基礎待ち時間
- $k_p$: 飽和付近でのqueue増加の強さ
- $g(\cdot)$: candidate固有のbacklogと容量圧力による補正

より直接的には、先行batch数と平均iteration時間を使って

$$
\boxed{
T_{\mathrm{queue},i,j,p}
\approx N_j^{\mathrm{batch\ ahead}}\overline{T}_{\mathrm{iter},j,p}
}
$$

とも表せる。

## 8. Routing待ち時間

Home GPUがadmit可能なら、routing待ちはrouting処理の固定時間のみとなる。

$$
\boxed{
T_{\mathrm{route},i}^{\mathrm{home}}
\approx t_0^{\mathrm{route}}
\quad(A_{i,\mathrm{home},p}=1)
}
$$

Home GPUがadmit不能なら、routing待ちはcapacityの再評価回数と再評価間隔に依存する。

$$
\boxed{
T_{\mathrm{route},i,j,p}
\approx t_0^{\mathrm{route}}
+N_i^{\mathrm{retry}}\Delta t_{\mathrm{retry}}
}
$$

$$
N_i^{\mathrm{retry}}
=h\!\left(\phi_{i,j,p},N_j^{\mathrm{wait}},N_j^{\mathrm{run}},\lambda_j\right)
$$

したがって、router待ちは独立した発生確率ではなく、capacity pressure、backlog、到着率によって決まる時間として扱う。

## 9. 通信時間

### 9.1 KV cache転送

再利用prefix $C_{ij}$ tokensを別GPUへ転送する場合、

$$
\boxed{
T_{\mathrm{KV},i,j,p}(C_{ij},B_{hj})
=t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token},p}}
{\eta_{\mathrm{KV}}10^6B_{hj}}
}
$$

となる。$B_{hj}$はMbps単位の帯域、$\eta_{\mathrm{KV}}$は競合やプロトコルを含む実効帯域係数である。

### 9.2 PP stage間通信

Stage間activationサイズを$S_{i,p}^{\mathrm{act}}$とすると、

$$
\boxed{
T_{\mathrm{PP},i,p}
=n_p^{\mathrm{link}}
\left(
t_{0,p}^{\mathrm{PP}}
+\frac{8S_{i,p}^{\mathrm{act}}}
{\eta_p^{\mathrm{PP}}10^6B_p^{\mathrm{PP}}}
\right)
}
$$

で近似する。$n_p^{\mathrm{link}}$はfirst token生成までのstage間通信回数である。

### 9.3 通信項全体

$$
\boxed{
T_{\mathrm{comm},i,j,p}
=I_{\mathrm{redirect}}
\left(T_{\mathrm{request},i,j}
+I_{\mathrm{KVmove}}T_{\mathrm{KV},i,j,p}\right)
+T_{\mathrm{PP},i,p}
}
$$

## 10. 統合したTTFT式

以上をまとめると、

$$
\boxed{
\begin{aligned}
TTFT_{i,j,p}\approx{}&
t_0^{\mathrm{route}}
+N_i^{\mathrm{retry}}\Delta t_{\mathrm{retry}}
\\
&+a_p\frac{\rho_{j,p}^{k_p}}{1-\rho_{j,p}}
g\!\left(N_j^{\mathrm{wait}},N_j^{\mathrm{run}},\phi_{i,j,p}\right)
\\
&+t_{0,p}^{\mathrm{pre}}
+k_p^{\mathrm{pre}}(B_{\mathrm{batch}})(L_i-C_{ij})
\\
&+I_{\mathrm{redirect}}
\left(T_{\mathrm{request},i,j}
+I_{\mathrm{KVmove}}T_{\mathrm{KV},i,j,p}\right)
+T_{\mathrm{PP},i,p}
\end{aligned}
}
$$

主要な依存関係は次の通りである。

```text
Input/output length
  -> KV必要量とprefill計算量を増加
  -> capacity pressure、queue、routing waitを増加

Reusable prefix length
  -> uncached prefill tokensを削減
  -> redirect時のKV転送量を増加

Arrival rate / waiting / running
  -> utilizationとbacklogを増加
  -> queue時間を非線形に増加

Pipeline parallel degree
  -> GPU当たりのmodel weightとKV/tokenを削減
  -> admission可能率を改善
  -> 論理instance数を削減
  -> PP通信とpipeline固定時間を追加
```

## 11. PP1とPP2の損益分岐

PP2がPP1よりTTFTを改善する条件は

$$
TTFT_{i,j,2}<TTFT_{i,j,1}
$$

であり、差分を整理すると

$$
\boxed{
\begin{aligned}
&(T_{\mathrm{route},1}-T_{\mathrm{route},2})
+(T_{\mathrm{queue},1}-T_{\mathrm{queue},2})
+(T_{\mathrm{redirect},1}-T_{\mathrm{redirect},2})
\\
&\qquad>
(T_{\mathrm{prefill},2}-T_{\mathrm{prefill},1})
+(T_{\mathrm{PPcomm},2}-T_{\mathrm{PPcomm},1})
\end{aligned}
}
$$

となる。すなわち、

$$
\boxed{
\text{PP2を選択}
\iff
\text{queue短縮}+\text{redirect削減}
>
\text{PP実行時間増分}+\text{PP通信コスト}
}
$$

である。

## 12. 実測から確認された依存関係

1. Inputが長いほどKV必要量とprefill時間が増える。
2. Prefix reuseが大きいほどprefill時間は減る。
3. Capacity pressureが1へ近づくと、admission failure、routing retry、queueが急増する。
4. PP2はGPU当たりのmodel weightとKV使用量を減らし、capacity pressureを緩和する。
5. PP2の利得は短いpromptでは小さく、PP1でqueueとredirectが増える領域で大きい。
6. 通信時間は転送データ量に比例し、実効帯域に反比例する。
7. 到着時点のsnapshotが同じでも、その後の新規到着によってqueue時間は増える。

係数$t_0$、$k$、$a$、$\eta$、$\mu$は環境ごとにprofileまたはsimulation結果から推定する。定式化の中心は係数値ではなく、上記の依存関係と損益分岐構造である。
