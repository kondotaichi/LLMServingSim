# TTFTの定式化

## 1. 出発点：TTFTの4成分分解

本定式化は、request $i$のTTFTを次の4成分へ分解するところから出発する。

$$
\boxed{
TTFT_i
=t_{\mathrm{route},i}
+t_{\mathrm{sched},i}
+t_{\mathrm{com},i}
+t_{\mathrm{KVtransfer},i}
}
$$

| 成分 | 意味 |
|---|---|
| $t_{\mathrm{route},i}$ | Router側でrouting先が確定するまでのqueue・待機時間 |
| $t_{\mathrm{sched},i}$ | 選択されたschedulerへ到着してから、最初のprefill batchへ入るまでのqueue時間 |
| $t_{\mathrm{com},i}$ | First token生成までの計算時間。`com`はcommunicationではなくcomputationを表す |
| $t_{\mathrm{KVtransfer},i}$ | Redirect時に、再利用するprefix KVを別GPUへ転送する時間 |

実際の計測にはrequest metadata転送や固定RTTなどの小さい通信時間も含まれる。これを明示する場合は

$$
TTFT_i
=t_{\mathrm{route},i}
+t_{\mathrm{sched},i}
+t_{\mathrm{com},i}
+t_{\mathrm{KVtransfer},i}
+t_{\mathrm{other},i}
$$

とする。ただし、主要な依存関係を議論するときは4成分へ簡略化する。

以下では、各成分がどのrequest情報・GPU状態・system構成に依存するかを順に定式化する。

## 2. 記号と単位

解析式では時間を秒、データサイズをbyte、ネットワーク帯域をMbit/sで表す。Fitting結果をmsで示す場合は、式で得た秒を$10^3$倍する。

### 2.1 添字

| 記号 | 説明 |
|---|---|
| $i$ | 対象requestのindex |
| $j$ | 配置先として評価するcandidate GPUまたはlogical instanceのindex |
| $h_i$ | Request $i$のhome GPU、すなわちprefix KVの転送元GPU |
| $p$ | Pipeline parallel degree。1 logical instanceを構成するGPU数 |

### 2.2 RequestとKV cache

| 記号 | 単位 | 説明 |
|---|---:|---|
| $L_i$ | tokens | Request $i$のinput、すなわちprompt token数 |
| $O_i$ | tokens | Request $i$の予定output token数。未知の場合は予測値または予約上限 |
| $C_{ij}$ | tokens | Candidate $j$で再利用できるrequest $i$の計算済みprefix token数 |
| $U_{ij}=L_i-C_{ij}$ | tokens | Prefillで新たに計算するinput token数 |
| $q$ | tokens/block | KV cache allocatorのblock size。LLMServingSimのデフォルトは16 |
| $N_{\mathrm{layer}}$ | layers | Model全体のTransformer layer数 |
| $N_{\mathrm{KVhead}}$ | heads/layer | 1 layer当たりのKV head数 |
| $d_{\mathrm{head}}$ | elements/head | 1 KV headのdimension |
| $b_{\mathrm{KV}}$ | bytes/element | KV cacheの1要素当たりbyte数。bf16は2、fp8は1 |
| $m_{\mathrm{KV/token}}$ | bytes/token | Model全体における1 token分のKV cacheサイズ |
| $M_{i,p}^{\mathrm{req}}$ | bytes/GPU | Request $i$が完了までに必要とするGPU当たりのKV容量 |
| $M_{j,p}^{\mathrm{active}}$ | bytes/GPU | Candidate $j$のactive requestsが将来必要とするKV容量 |
| $M_p^{\mathrm{KV}}$ | bytes/GPU | PP度$p$におけるGPU当たりのKV budget |

### 2.3 Queueと負荷

| 記号 | 単位 | 説明 |
|---|---:|---|
| $N_j^{\mathrm{wait}}$ | requests | Candidate $j$のschedulerで待機中のrequest数 |
| $N_j^{\mathrm{run}}$ | requests | Candidate $j$で実行中のrequest数 |
| $G$ | GPUs | System全体のGPU数 |
| $R_p=G/p$ | instances | PP度$p$で構成できるlogical instance数 |
| $\lambda$ | requests/second | System全体へのrequest arrival rate |
| $\lambda_{j,p}$ | requests/second | PP度$p$のcandidate $j$へのarrival rate |
| $\rho_{j,p}$ | dimensionless | Candidate $j$の負荷率 |
| $\phi_{i,j,p}$ | dimensionless | Request $i$をcandidate $j$へ追加した場合のKV capacity pressure |

## 3. 計算時間 $t_{\mathrm{com}}$

### 3.1 基本的な依存関係

First tokenまでに新たに計算するinput token数は

$$
\boxed{
U_{ij}=L_i-C_{ij}
}
$$

である。実効計算throughputを$B_{\mathrm{com},p}$とすると、計算時間は「固定時間＋処理token数／実効処理速度」として

$$
\boxed{
t_{\mathrm{com},i,j,p}
=t_{0,p}^{\mathrm{com}}
+\frac{L_i-C_{ij}}
{B_{\mathrm{com},p}}
}
$$

と表す。$B_{\mathrm{com},p}$はbatch sizeそのものではなく、GPUが1秒間に実効的に処理できるtoken数である。GPU、model、batch size、PP度などの影響は、この実効throughputへ含める。

| 記号 | 単位 | 説明 |
|---|---:|---|
| $t_{0,p}^{\mathrm{com}}$ | seconds | Kernel launchやpipeline fillを含む固定計算時間 |
| $B_{\mathrm{com},p}$ | tokens/second | GPU、model、batch size、PP度を反映した実効token処理throughput |

$O_i$は現在のrequestのfirst token計算量へ直接強く影響するわけではない。しかし、KV予約量とrequestのsystem滞在時間を変えるため、$t_{\mathrm{route}}$と$t_{\mathrm{sched}}$へ間接的に影響する。

## 4. KV容量とadmission

### 4.1 1 token分のKV cache

Model全体における1 token分のKV cacheサイズは

$$
\boxed{
m_{\mathrm{KV/token}}
=2N_{\mathrm{layer}}N_{\mathrm{KVhead}}d_{\mathrm{head}}b_{\mathrm{KV}}
}
$$

である。係数2はKeyとValueを表す。PPによってlayerを均等分割する場合、GPU当たりのサイズは

$$
\boxed{
m_{\mathrm{KV/token},p}
=\frac{m_{\mathrm{KV/token}}}{p}
}
$$

となる。この関係は均等なlayer配置の近似である。TPによるKV head shardingは別途考慮する。

### 4.2 Requestが必要とするKV容量

Request $i$の最終sequence長をKV block単位へ切り上げると、必要KV容量は

$$
\boxed{
M_{i,p}^{\mathrm{req}}
=q\left\lceil\frac{L_i+O_i}{q}\right\rceil
m_{\mathrm{KV/token},p}
}
$$

となる。$q\lceil(L_i+O_i)/q\rceil$はblock単位へ切り上げたtoken slot数である。

### 4.3 Capacity pressure

Request $i$をcandidate $j$へ追加した場合のcapacity pressureを

$$
\boxed{
\phi_{i,j,p}
=\frac{M_{j,p}^{\mathrm{active}}+M_{i,p}^{\mathrm{req}}}
{M_p^{\mathrm{KV}}}
}
$$

とする。$\phi_{i,j,p}>1$では予測KV需要がbudgetを超えるため、即時admissionできず、routing待ちまたはredirectが必要になる。

## 5. Router queue $t_{\mathrm{route}}$

### 5.1 基本的な依存関係

Router queueは、home GPUまたはredirect候補がrequestをadmitできるまでの待ち時間である。ここで重要なのは、routing候補集合にhome GPU自身も含めることである。

$$
\boxed{
\mathcal J_i=\{h_i\}\cup\mathcal J_i^{\mathrm{redirect}}
}
$$

したがって、別instanceへ移送しない場合も、home GPU $h_i$をrouting先候補として同じ式で評価する。

まず、request $i$をcandidate $j$へ配置するために不足しているKV容量を

$$
\boxed{
D_{i,j,p}^{\mathrm{KV}}
=\left[
M_{j,p}^{\mathrm{active}}
+M_{i,p}^{\mathrm{req}}
-M_p^{\mathrm{KV}}
\right]_+
}
$$

と定義する。$[x]_+=\max(0,x)$であり、$D_{i,j,p}^{\mathrm{KV}}=0$ならcandidate $j$は即時admission可能である。

Candidate $j$でKV容量が実効的に回復する速度を$B_{\mathrm{KVfree},j,p}$とすると、routing待ちは「KV不足量／KV容量回復速度」として

$$
\boxed{
t_{\mathrm{route},i,j,p}
\approx
t_0^{\mathrm{route}}
+\frac{D_{i,j,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,p}}
}
$$

と表せる。Home GPUを含む複数のrouting候補$\mathcal J_i$から、最も早く収容可能になるcandidateを

$$
\boxed{
j_i^*
=\mathop{\arg\min}_{j\in\mathcal J_i}
\frac{D_{i,j,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,p}}
}
$$

として選ぶ場合、router queueは

$$
\boxed{
t_{\mathrm{route},i,p}
\approx
t_0^{\mathrm{route}}
+\min_{j\in\mathcal J_i}
\frac{D_{i,j,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j,p}}
}
$$

となる。

この式には次の2つの場合が含まれる。

$$
\boxed{
\begin{cases}
j_i^*=h_i:
&\text{home GPUのcapacity解放を待つ。KV移送は行わない}\\
j_i^*\neq h_i:
&\text{redirect先のcapacity解放を待ち、必要ならKVを移送する}
\end{cases}
}
$$

したがって、$j_i^*=h_i$かつ$D_{i,h_i,p}^{\mathrm{KV}}>0$なら、**KVを移送しなくてもrouter queueは正になる**。一方、KV transferはrouting先が決定した後にのみ発生する別成分である。

$B_{\mathrm{KVfree},j,p}$は、running requestsの完了によるKV解放と、新規requestによるKV消費の差として概念的に

$$
\boxed{
B_{\mathrm{KVfree},j,p}
=\left[
\mu_{j,p}^{\mathrm{complete}}\overline{M}_{j,p}^{\mathrm{release}}
-\lambda_{j,p}\overline{M}_{p}^{\mathrm{req}}
\right]_+
}
$$

と表せる。

| 記号 | 単位 | 説明 |
|---|---:|---|
| $t_0^{\mathrm{route}}$ | seconds | Routing処理自体の固定時間 |
| $D_{i,j,p}^{\mathrm{KV}}$ | bytes | Request $i$をcandidate $j$へ配置するために不足しているKV容量 |
| $B_{\mathrm{KVfree},j,p}$ | bytes/second | Candidate $j$で利用可能KV容量が実効的に回復する速度 |
| $\mathcal J_i$ | candidates | Home GPU $h_i$とredirect候補を合わせたcandidate集合 |
| $j_i^*$ | instance | Request $i$について選択されたrouting先。$j_i^*=h_i$なら移送しない |
| $\mu_{j,p}^{\mathrm{complete}}$ | requests/second | Candidate $j$におけるrequest完了率 |
| $\overline{M}_{j,p}^{\mathrm{release}}$ | bytes/request | 1 requestの完了によって解放される平均KV容量 |
| $\overline{M}_{p}^{\mathrm{req}}$ | bytes/request | 新規request 1件が消費する平均KV容量 |

$D_{i,j,p}^{\mathrm{KV}}>0$かつ$B_{\mathrm{KVfree},j,p}=0$の場合、現在の負荷状態が続く限りcandidate $j$ではcapacity不足を解消できない。実装上はrequestをpending queueへ戻し、状態が変化した時点でcapacityを再評価する。

## 6. Scheduler queue $t_{\mathrm{sched}}$

総GPU数$G$に対し、PP度$p$で構成できるlogical instance数は

$$
R_p=\frac{G}{p}
$$

である。負荷が均等に分散される場合、

$$
\lambda_{j,p}\approx\frac{\lambda}{R_p}
$$

となる。Candidate $j$へ到着する1 request当たりの平均未計算token数を$\overline{U}_{j,p}$とすると、token workloadに基づく負荷率は

$$
\boxed{
\rho_{j,p}
=\frac{\lambda_{j,p}\overline{U}_{j,p}}
{B_{\mathrm{com},p}}
}
$$

である。

Request $i$より先にschedulerが処理すべきtoken workloadを

$$
\boxed{
W_{\mathrm{ahead},i,j}
=\sum_{r\in\mathcal Q_{i,j}}
U_{rj}^{\mathrm{remain}}
+W_{\mathrm{run},j}
}
$$

と定義する。$\mathcal Q_{i,j}$はcandidate $j$でrequest $i$より先に待っているrequest集合であり、$W_{\mathrm{run},j}$は実行中batchの残りworkloadである。

Schedulerがbacklogを減らす実効速度は、GPUの処理throughputから新規到着workloadを引いた

$$
\boxed{
B_{\mathrm{schedfree},j,p}
=\left[
B_{\mathrm{com},p}
-\lambda_{j,p}\overline{U}_{j,p}
\right]_+
=B_{\mathrm{com},p}[1-\rho_{j,p}]_+
}
$$

である。したがってscheduler queueは「先行workload／backlog解消速度」として

$$
\boxed{
t_{\mathrm{sched},i,j,p}
\approx
t_0^{\mathrm{sched}}
+\frac{W_{\mathrm{ahead},i,j}}
{B_{\mathrm{schedfree},j,p}}
}
$$

| 記号 | 単位 | 説明 |
|---|---:|---|
| $\overline{U}_{j,p}$ | tokens/request | Candidate $j$へ到着する1 request当たりの平均未計算token数 |
| $\mathcal Q_{i,j}$ | requests | Candidate $j$でrequest $i$より先に待っているrequest集合 |
| $U_{rj}^{\mathrm{remain}}$ | tokens | 先行request $r$に残っている未計算token数 |
| $W_{\mathrm{run},j}$ | tokens | Candidate $j$で実行中のbatchに残っているtoken-equivalent workload |
| $W_{\mathrm{ahead},i,j}$ | tokens | Request $i$より先に処理されるtoken-equivalent workload |
| $B_{\mathrm{schedfree},j,p}$ | tokens/second | Candidate $j$でscheduler backlogが実効的に減少する速度 |
| $t_0^{\mathrm{sched}}$ | seconds | Scheduler処理と次のiteration境界に伴う固定待ち時間 |

$W_{\mathrm{ahead},i,j}>0$かつ$B_{\mathrm{schedfree},j,p}=0$の場合、現在の到着率と処理速度が続く限りbacklogは減少しない。この式はcontinuous batchingを平均token flowとして近似したfluid modelである。

### 6.1 $B_{\mathrm{com}}$、$B_{\mathrm{schedfree}}$、$B_{\mathrm{KVfree}}$の関係

三つの$B$はすべて実効的な処理速度を表すが、同じ種類の帯域ではない。

| 記号 | 単位 | 意味 |
|---|---:|---|
| $B_{\mathrm{com},p}$ | tokens/second | Logical instanceが持つ実効計算処理能力 |
| $B_{\mathrm{schedfree},j,p}$ | tokens/second | 新規流入workloadを処理した後に、scheduler backlogを減らすために残る計算能力 |
| $B_{\mathrm{KVfree},j,p}$ | bytes/second | Request完了によるKV解放から新規KV需要を引いた、正味のKV容量回復速度 |

このうち基本となるのは$B_{\mathrm{com},p}$である。Schedulerへ流入する計算workloadは

$$
\lambda_{j,p}\overline U_{j,p}
\quad[\mathrm{tokens/second}]
$$

であるため、scheduler backlogを減らすために利用できる余剰計算能力は

$$
\boxed{
B_{\mathrm{schedfree},j,p}
=\left[
B_{\mathrm{com},p}
-\lambda_{j,p}\overline U_{j,p}
\right]_+
}
$$

となる。したがって、

$$
\boxed{
0\le B_{\mathrm{schedfree},j,p}\le B_{\mathrm{com},p}
}
$$

であり、$B_{\mathrm{schedfree}}$は$B_{\mathrm{com}}$の直接的な余剰量と解釈できる。

一方、$B_{\mathrm{KVfree}}$は計算速度そのものではなく、KV cacheの空き容量が正味で増える速度である。

$$
\boxed{
B_{\mathrm{KVfree},j,p}
=\left[
\mu_{j,p}^{\mathrm{complete}}
\overline M_{j,p}^{\mathrm{release}}
-\lambda_{j,p}\overline M_p^{\mathrm{req}}
\right]_+
}
$$

$B_{\mathrm{com}}$と$B_{\mathrm{KVfree}}$は、request完了率$\mu^{\mathrm{complete}}$を介して間接的につながる。1 requestを完了するための平均計算workloadを$\overline W_{j,p}^{\mathrm{request}}$ tokens/requestとすれば、粗いfluid近似として

$$
\mu_{j,p}^{\mathrm{complete}}
\approx
\frac{B_{\mathrm{com},p}}
{\overline W_{j,p}^{\mathrm{request}}}
$$

と置ける。したがって、

$$
\boxed{
B_{\mathrm{KVfree},j,p}
\approx
\left[
\frac{B_{\mathrm{com},p}}
{\overline W_{j,p}^{\mathrm{request}}}
\overline M_{j,p}^{\mathrm{release}}
-\lambda_{j,p}\overline M_p^{\mathrm{req}}
\right]_+
}
$$

となる。すなわち、関係性は概念的に

$$
\boxed{
B_{\mathrm{com}}
\longrightarrow
\begin{cases}
B_{\mathrm{schedfree}}:
&\text{計算backlogを減らす速度}\\
\mu^{\mathrm{complete}}
\longrightarrow B_{\mathrm{KVfree}}:
&\text{KV容量を回復する速度}
\end{cases}
}
$$

と整理できる。

$B_{\mathrm{schedfree}}$と$B_{\mathrm{KVfree}}$の間に直接的な等式はない。前者はtokens/second、後者はbytes/secondであり、そのまま比較や加減算はできない。また、$B_{\mathrm{schedfree}}$が小さいとrequestの処理開始と完了が遅れ、結果として$\mu^{\mathrm{complete}}$と$B_{\mathrm{KVfree}}$も小さくなる可能性があるが、その関係はKV容量を予約するタイミングにも依存する。

さらに、実際のcontinuous batchingではprefillとdecodeが混在し、requestごとのoutput長も異なる。そのため、$\mu^{\mathrm{complete}}\approx B_{\mathrm{com}}/\overline W^{\mathrm{request}}$は概念的な近似であり、正確な$B_{\mathrm{KVfree}}$はrequest完了時刻とKV解放量から直接推定する必要がある。

## 7. KV transfer $t_{\mathrm{KVtransfer}}$

Prefix KVの転送量は、再利用するprefix token数と1 token分のKV cacheサイズの積である。

$$
S_{\mathrm{KV},i,j,p}
=C_{ij}m_{\mathrm{KV/token},p}
$$

したがってKV転送時間は

$$
\boxed{
t_{\mathrm{KVtransfer},i,j,p}
=\mathbf{1}[j_i^*\neq h_i]
I_{\mathrm{KVmove},i}
\left(
t_0^{\mathrm{KV}}
+\frac{8C_{ij}m_{\mathrm{KV/token},p}}
{\eta_{\mathrm{KV}}10^6B_{h_i j}}
\right)
}
$$

となる。

### 7.1 KV転送式の導出と係数の由来

この式は、基本的な通信時間

$$
\boxed{
\text{転送時間}
=\text{固定遅延}
+\frac{\text{転送データ量}}{\text{実効帯域}}
}
$$

から導かれる。Request $i$についてcandidate $j$へ移送するprefixが$C_{ij}$ tokensであるとき、GPU当たりの転送データ量は

$$
\boxed{
S_{\mathrm{KV},i,j,p}
=C_{ij}m_{\mathrm{KV/token},p}
}
$$

bytesとなる。ここで、

$$
m_{\mathrm{KV/token},p}
=\frac{2N_{\mathrm{layer}}N_{\mathrm{KVhead}}d_{\mathrm{head}}b_{\mathrm{KV}}}{p}
$$

に含まれる係数2はKeyとValue、$1/p$はPP stage間でlayerを均等分割する近似を表す。

一方、$S_{\mathrm{KV},i,j,p}$の単位はbytes、$B_{h_i j}$の単位はMbit/sである。この単位を揃えるため、

$$
1\ \mathrm{byte}=8\ \mathrm{bits},
\qquad
1\ \mathrm{Mbit/s}=10^6\ \mathrm{bit/s}
$$

を用いる。したがって、データ量に比例する転送時間は

$$
\frac{8S_{\mathrm{KV},i,j,p}}
{\eta_{\mathrm{KV}}10^6B_{h_i j}}
=
\frac{8C_{ij}m_{\mathrm{KV/token},p}}
{\eta_{\mathrm{KV}}10^6B_{h_i j}}
$$

secondsとなる。分子の8はbyteからbitへの変換、分母の$10^6$はMbit/sからbit/sへの変換であり、fittingによって得る係数ではない。

$\eta_{\mathrm{KV}}$は、protocol overheadや同期などによって理論帯域を完全には利用できないことを表す実効帯域係数である。

$$
\boxed{
B_{h_i j}^{\mathrm{effective}}
=\eta_{\mathrm{KV}}10^6B_{h_i j}
}
$$

ここで$B_{h_i j}^{\mathrm{effective}}$をbit/sで表している。$\eta_{\mathrm{KV}}$は通常$0<\eta_{\mathrm{KV}}\leq1$であり、実測から推定する。

二つの指示変数は、次の条件を表す。

$$
\mathbf{1}[j_i^*\neq h_i]
=
\begin{cases}
0 & j_i^*=h_i\\
1 & j_i^*\neq h_i
\end{cases}
$$

したがって、home GPUで処理する場合はKV転送時間が0となる。また、別instanceへredirectしてもKVを移送せずprefixを再計算する場合は$I_{\mathrm{KVmove},i}=0$となる。

帯域を最初からbytes/secondの実効帯域$\widetilde B_{h_i j}^{\mathrm{KV}}$として定義すれば、単位変換を式の外へ出して

$$
\boxed{
t_{\mathrm{KVtransfer},i,j,p}
=\mathbf{1}[j_i^*\neq h_i]
I_{\mathrm{KVmove},i}
\left(
t_0^{\mathrm{KV}}
+\frac{C_{ij}m_{\mathrm{KV/token},p}}
{\widetilde B_{h_i j}^{\mathrm{KV}}}
\right)
}
$$

と簡潔に書ける。概念的には、この簡略形も元の式と同じ意味である。

| 記号 | 単位 | 説明 |
|---|---:|---|
| $S_{\mathrm{KV},i,j,p}$ | bytes | 転送するprefix KVのデータ量 |
| $\mathbf{1}[j_i^*\neq h_i]$ | 0 or 1 | Routing先がhome GPUと異なる場合に1となる指示変数 |
| $I_{\mathrm{KVmove},i}$ | 0 or 1 | Prefix KVを転送する場合に1となる指示変数 |
| $t_0^{\mathrm{KV}}$ | seconds | KV転送開始の固定遅延 |
| $B_{h_i j}$ | Mbit/s | Home GPU $h_i$からcandidate $j$への帯域 |
| $\eta_{\mathrm{KV}}$ | dimensionless | KV転送の実効帯域係数。通常$0<\eta_{\mathrm{KV}}\le1$ |

分子の8はbyteをbitへ、分母の$10^6$はMbit/sをbit/sへ変換する。

## 8. 4成分を展開した統合式

以上を最初の4成分分解へ代入すると、

$$
\boxed{
\begin{aligned}
TTFT_{i,j_i^*,p}\approx{}&
\underbrace{
t_0^{\mathrm{route}}
+\frac{D_{i,j_i^*,p}^{\mathrm{KV}}}
{B_{\mathrm{KVfree},j_i^*,p}}
}_{t_{\mathrm{route}}}
\\
&+\underbrace{
t_0^{\mathrm{sched}}
+\frac{W_{\mathrm{ahead},i,j_i^*}}
{B_{\mathrm{schedfree},j_i^*,p}}
}_{t_{\mathrm{sched}}}
\\
&+\underbrace{
t_{0,p}^{\mathrm{com}}
+\frac{L_i-C_{i,j_i^*}}
{B_{\mathrm{com},p}}
}_{t_{\mathrm{com}}}
\\
&+\underbrace{
\mathbf{1}[j_i^*\neq h_i]I_{\mathrm{KVmove},i}
\left(
t_0^{\mathrm{KV}}
+\frac{8C_{i,j_i^*}m_{\mathrm{KV/token},p}}
{\eta_{\mathrm{KV}}10^6B_{h_i,j_i^*}}
\right)
}_{t_{\mathrm{KVtransfer}}}
\end{aligned}
}
$$

となる。

```text
TTFT
├── Router queue
│   └── capacity pressure、candidate backlog、arrival rate
├── Scheduler queue
│   └── utilization、waiting/running requests、capacity pressure
├── Computation
│   └── input tokens、cached prefix tokens、batch tokens、PP degree
└── KV transfer
    └── transferred prefix tokens、KV bytes/token、network bandwidth
```

## 9. PP度の影響

PP度$p$は4成分へ異なる方向に作用する。

- $m_{\mathrm{KV/token},p}=m_{\mathrm{KV/token}}/p$となり、GPU当たりのKV需要を減らす
- Model weightがstageへ分割され、$M_p^{\mathrm{KV}}$を増やす
- Logical instance数$R_p=G/p$を減らし、instance当たりarrival rateを増やす
- Pipeline partitionとfillにより$t_{\mathrm{com}}$を変化させる
- PP stage間通信を$t_{\mathrm{other}}$へ追加する

したがってPP2がPP1よりTTFTを改善する条件は、

$$
\boxed{
\text{PP2を選択}
\iff
\text{route・scheduler queue短縮}
+\text{redirect削減}
>
\text{computation増分}
+\text{PP追加コスト}
}
$$

である。

## 10. Fitting

係数$t_0$、$k$、$a$、$\eta$と関数$f$、$g$は、model、GPU、workload、parallelismごとに実測から推定する。

単純な4成分式の実データへのfitting結果と図は[`simple_formula_fit_report.md`](simple_formula_fit_report.md)を参照する。

より柔軟な依存関係を用いた分析は[`dependency_fit_report.md`](dependency_fit_report.md)を参照する。

PP1とPP2の損益分岐条件は[`pp_break_even_report.md`](pp_break_even_report.md)を参照する。

KV移送あり・なしの損益分岐条件は[`kvtransfer_break_even_report.md`](kvtransfer_break_even_report.md)を参照する。

両者の相互作用を含む統合版は[`pp_kvtransfer_break_even_report.md`](pp_kvtransfer_break_even_report.md)を参照する。
