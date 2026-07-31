# PP2によるTTFT改善の条件と損益分岐モデル

> Note: 現在の4成分定式化からPP1・PP2とKV転送有無を一貫して導いた分析は、[`pp_kvtransfer_break_even_report.md`](pp_kvtransfer_break_even_report.md)を参照する。本ファイルは以前の実測中心の分析として残す。

## 1. 目的

本レポートでは、10 GPUをPP1の10レプリカとして使う構成と、PP2の5グループとして使う構成を比較し、PP2がTTFT（Time To First Token）を改善する条件を整理する。また、PPを使うかどうかを判断するための損益分岐式と、追加実験で推定すべきパラメータを示す。

実測値は主に`experiments/2026-07-28_pp_schedule_conceal_and_speculative`の完走済み300リクエストアームに基づく。改善率が正の場合、PP2がPP1より良いことを表す。

## 2. 実測結果から得られた結論

今回のPP2は、1リクエストの計算そのものを常に高速化する方式ではない。主な利得は、モデルを2 GPUに分割することでGPU当たりのモデル重量を減らし、KV cacheやbatchの収容余力を増やすことから生じる。

この収容余力により、PP1で発生していたscheduler queue待ちとcapacity redirectが減り、その削減量がPP通信・同期などの追加コストを上回る場合にTTFTが改善する。

```text
モデルを2 GPUに分割
  -> GPU当たりのモデル重量が減少
  -> KV cacheとbatchの収容余力が増加
  -> scheduler queue待ちとcapacity redirectが減少
  -> 平均TTFTおよびtail TTFTが改善
```

### 2.1 観測された改善

| ワークロード | 平均TTFT改善率 | p95改善率 | redirect件数（PP1→PP2） | 解釈 |
|---|---:|---:|---:|---|
| 512 / reuse 0% | +5.6% | +8.5% | 0→0 | 小幅改善 |
| 2000 / reuse 25% | +0.3% | -17.8% | 0→0 | 平均はほぼ損益分岐、p95は悪化 |
| 6000 / reuse 50% | +3.2% | +12.6% | 18→0 | redirect解消による小幅改善 |
| 8000 / reuse 25% | +42.8% | +64.2% | 183→35 | PP2が明確に有利 |
| 8000 / reuse 50% | +20.4% | +35.5% | 106→12 | PP2が明確に有利 |
| 混合 / バースト | +5.1% | +5.0% | 17→0 | バースト時の容量圧力を緩和 |

完走した全ての対応比較で、PP2はPP1より平均scheduler queue時間を短縮した。特に8,000トークン級ではredirect件数も大きく減少し、平均だけでなくp95やp99のTTFTも改善した。

一方、TPOTとend-to-end完了時間は全ての完走比較で悪化した。したがって、今回のPP2はTTFTとtail latencyの最適化であり、decode全体の高速化ではない。

## 3. PP2が有利になりやすい条件

PP2によるTTFT改善が期待できるのは、次の条件である。

1. promptが長く、KV cache需要が大きい。
2. 同時実行数や到着率が高く、PP1のscheduler queueが長い。
3. PP1でcapacity redirectが頻発している。
4. KV cache、token budget、sequence slotのいずれかがadmissionの制約になっている。
5. p95やp99 TTFTが平均より大きく膨らんでおり、容量不足によるtailが発生している。
6. バースト負荷により、一時的にPP1の収容能力を超える。

逆に、短いprompt、低い到着率、十分なKV空き容量、redirectが発生しない条件では、PP2の利得は小さい。この領域では、PP通信、stage間同期、論理インスタンス数の減少といったコストが相対的に大きくなる。

今回のデータでは8,000トークン付近で改善が大きくなったが、「入力長8,000」が普遍的な境界という意味ではない。本質的な境界は、PP1の容量圧力によってqueueとredirectが非線形に増え始める点である。

## 4. TTFTの基本分解

PP度を\(p\)とし、TTFTを次のように分解する。

\[
TTFT_p = W_{\mathrm{queue},p}
       + T_{\mathrm{prefill},p}
       + T_{\mathrm{comm},p}
       + P_{\mathrm{redirect},p} C_{\mathrm{redirect},p}
\]

各項の意味は次の通りである。

- \(W_{\mathrm{queue},p}\): scheduler queue待ち時間
- \(T_{\mathrm{prefill},p}\): 最初のtokenを生成するまでのprefill計算時間
- \(T_{\mathrm{comm},p}\): PP通信およびstage間同期の時間
- \(P_{\mathrm{redirect},p}\): capacity redirectが発生する確率
- \(C_{\mathrm{redirect},p}\): redirect 1件当たりの追加時間

redirectコストは、さらに次のように分解できる。

\[
C_{\mathrm{redirect},p}
= T_{\mathrm{route},p}
+ T_{\mathrm{KV},p}
+ T_{\mathrm{target\_queue},p}
\]

## 5. PP1とPP2の損益分岐式

PP2を選ぶ条件は、単純には次式である。

\[
TTFT_2 < TTFT_1
\]

基本分解を代入すると、次の形になる。

\[
\underbrace{W_{\mathrm{queue},1}-W_{\mathrm{queue},2}}_{\text{queue短縮}}
+
\underbrace{
P_{\mathrm{redirect},1}C_{\mathrm{redirect},1}
-P_{\mathrm{redirect},2}C_{\mathrm{redirect},2}
}_{\text{redirect削減}}
>
\underbrace{
(T_{\mathrm{prefill},2}-T_{\mathrm{prefill},1})
+T_{\mathrm{comm},2}
}_{\text{PP2の追加コスト}}
\]

したがって、PP2の損益分岐点は次のように説明できる。

> PP2によるqueue待ちとredirectコストの削減量が、PP2によるprefill実行時間の増分と通信・同期コストを上回る点。

実際のPP1にも通信コストが存在する場合は、右辺をより一般的に
\((T_{\mathrm{prefill},2}+T_{\mathrm{comm},2})-(T_{\mathrm{prefill},1}+T_{\mathrm{comm},1})\)
と置く。

## 6. 論理インスタンス数とqueueモデル

総GPU数を\(G\)、PP度を\(p\)とすると、論理インスタンス数は

\[
R_p = \frac{G}{p}
\]

となる。今回の10 GPU構成では、

\[
R_1=10,\qquad R_2=5
\]

である。

到着率を\(\lambda\)、1論理インスタンス当たりの実効処理率を\(\mu_p^{\mathrm{eff}}\)とすると、負荷率は概略

\[
\rho_p = \frac{\lambda}{R_p\mu_p^{\mathrm{eff}}}
\]

と表せる。簡易的なqueue待ち時間モデルとしては、

\[
W_{\mathrm{queue},p}
\approx
a_p\frac{\rho_p^{k_p}}{1-\rho_p}
\]

のような非線形式を利用できる。\(\rho_p\)が1へ近づくと待ち時間が急増するため、損益分岐点も入力長や到着率に対して非線形になる。

ただし、PP2では論理インスタンス数が半減する一方、GPU当たりのモデル重量が減り、KV cacheおよびbatchの収容能力が増える。そのため、\(\mu_p^{\mathrm{eff}}\)には計算速度だけでなくadmission可能率を含める必要がある。

\[
\mu_p^{\mathrm{eff}}
= \mu_p A_p(L,B,H,M_{\mathrm{free}})
\]

- \(L\): prompt長
- \(B\): 同時実行リクエスト数
- \(H\): prefix reuse率
- \(M_{\mathrm{free}}\): KV cacheの空き容量
- \(A_p\): メモリ・token budget・sequence slot制約下でadmitできる割合

## 7. メモリ容量境界

モデル重量がPP stage間で均等に分割される近似では、GPU当たりのモデル重量は

\[
M_{\mathrm{weights},p}\approx\frac{M_{\mathrm{model}}}{p}
\]

となる。

KV cache需要を簡略化して、

\[
M_{\mathrm{KV}}
\approx
B L_{\mathrm{effective}}m_{\mathrm{KV/token}}
\]

と置く。prefix reuseの効果を単純化すると、

\[
L_{\mathrm{effective}}=L(1-H)
\]

である。したがって、PP度\(p\)でadmit可能な近似条件は、

\[
\frac{M_{\mathrm{model}}}{p}
+BL(1-H)m_{\mathrm{KV/token}}
+M_{\mathrm{activations}}(B)
\le M_{\mathrm{GPU}}
\]

となる。

PP1ではこの不等式を満たさず、PP2では満たす領域が、PP2の容量面での主な勝ち筋である。ただし、実際のprefix cachingでは共有prefixのKVをリクエストごとに単純加算できないため、最終モデルではシミュレータが出力する実KV使用量を用いる方が正確である。

## 8. 実測データによる判定モデル

運用時に使いやすい判定値として、

\[
y = TTFT_{PP1}-TTFT_{PP2}
\]

を定義する。\(y>0\)ならPP2が有利、\(y<0\)ならPP1が有利である。

まずは次の回帰式が候補になる。

\[
y = \beta_0
+\beta_1L
+\beta_2\lambda
+\beta_3W_{\mathrm{queue},1}
+\beta_4P_{\mathrm{redirect},1}
+\beta_5M_{\mathrm{pressure},1}
+\beta_6H
+\epsilon
\]

ここで、\(M_{\mathrm{pressure},1}\)には例えば
\(1-M_{\mathrm{free},1}/M_{\mathrm{KV,total},1}\)を使用できる。

今回の結果では長いpromptで改善幅が急増しているため、線形式だけでなくhinge特徴量を導入する。

\[
(L-L_0)_+=\max(0,L-L_0)
\]

\[
y = \beta_0
+\beta_1L
+\beta_2(L-L_0)_+
+\beta_3W_{\mathrm{queue},1}
+\beta_4P_{\mathrm{redirect},1}
+\beta_5M_{\mathrm{pressure},1}
+\epsilon
\]

境界\(L_0\)は固定せず、cross-validationで探索する。さらに、入力長だけでなく容量圧力に対するhinge
\((M_{\mathrm{pressure},1}-M_0)_+\)も比較する。物理的には、入力長より容量圧力に対するhingeの方が異なるモデルやGPUへ一般化しやすいと予想される。

平均TTFTとtail TTFTでは損益分岐点が異なる可能性があるため、目的変数は少なくともmean、p95、p99について別々に推定する。また、改善率を目的変数にすると小さいTTFTのケースを過大評価しやすいため、学習時には絶対時間差\(TTFT_{PP1}-TTFT_{PP2}\)を基本とし、改善率は報告指標として併記する。

## 9. 暫定的な判断規則

現時点では、以下の2段階ルールが解釈しやすい。

1. PP1で対象リクエストをadmitできるか判定する。
2. admit可能ならqueue短縮量とPP追加コストを比較し、admit不能ならredirect回避効果とPP追加コストを比較する。

\[
\begin{aligned}
&\text{PP1でadmit可能:} &&
W_{\mathrm{queue},1}-W_{\mathrm{queue},2}>C_{\mathrm{PP}}
\text{ならPP2}\\
&\text{PP1でadmit不能:} &&
C_{\mathrm{redirect},1}-C_{\mathrm{redirect},2}>C_{\mathrm{PP}}
\text{ならPP2}
\end{aligned}
\]

ここで、

\[
C_{\mathrm{PP}}
=
(T_{\mathrm{prefill},2}+T_{\mathrm{comm},2})
-(T_{\mathrm{prefill},1}+T_{\mathrm{comm},1})
\]

である。

実装上の初期ルールとしては、次の状態をPP2推奨シグナルとして利用できる。

- PP1のcapacity redirect率が一定値を超える。
- scheduler queue時間が推定PP追加コストを超える。
- KV cache空き率が一定値を下回る。
- 入力長と同時実行数から計算した予測KV需要がPP1の空き容量を超える。
- p95またはp99 TTFTがSLOへ接近している。

## 10. 損益分岐点を求める追加実験

現在のデータは入力長、reuse率、到着率の組み合わせが少なく、厳密な境界推定には不足している。次の軸を系統的に掃引する。

| 軸 | 推奨値の例 | 目的 |
|---|---|---|
| 入力長 | 512, 1000, 2000, 4000, 6000, 7000, 8000, 9000 | 長さに対する非線形境界の推定 |
| 到着率 | 低負荷から飽和点超過まで | queueの発散点を特定 |
| prefix reuse率 | 0, 0.25, 0.5, 0.75 | KV需要と計算量の効果を分離 |
| 出力長 | 短・中・長 | decode滞在時間による容量占有を評価 |
| burst強度 | 定常、2倍、4倍 | tail TTFTへの影響を評価 |
| max_num_seqs | 複数水準 | memory制約とslot制約を分離 |
| KV cache dtype | bf16、fp8など | KV容量境界の移動を確認 |

各条件でPP1とPP2へ同一workloadを与え、少なくとも以下を保存する。

- リクエスト単位のTTFTとその内訳
- scheduler queue時間
- redirect有無とKV migration時間
- 到着時点のrunning、waiting、KV cache使用量
- admit不能となった理由
- prompt長、出力長、prefix cache hit量
- 論理インスタンスごとのutilization
- PP通信・同期時間

同一リクエストをPP1とPP2で対応付け、\(TTFT_{PP1}-TTFT_{PP2}\)を計算する。境界付近では試行seedを増やし、平均だけでなく信頼区間と誤選択率も評価する。

## 11. 現時点でのまとめ

PP2の利得は、prompt長そのものではなく、PP1で発生する容量圧力、scheduler queue、capacity redirectによって決まる。今回の実測では8,000トークン級でPP2が明確に有利になったが、これはPP1のqueueとredirectが急増した結果である。

したがって、損益分岐点は次の釣り合いとして定義するのが妥当である。

\[
\boxed{
\text{queue短縮} + \text{redirect削減}
=
\text{PP実行時間増分} + \text{PP通信・同期コスト}
}
\]

この式を出発点に、入力長、到着率、reuse率、KV容量圧力を掃引して各項を実測すれば、モデル・GPU・SLOごとのPP1/PP2選択境界を求められる。
