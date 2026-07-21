# Multi-candidateがKV handoffより効いた理由

## 結論

Multi-candidateの主な改善要因は、TTFT予測モデルそのものよりも、redirect候補を
第2近傍GPUの1台に固定せず、収容可能な全候補から選んだことである。
単純KV handoffが特定GPUへ負荷を集中させたのに対し、Multi-candidateはredirect先を分散し、
不要なredirectも減らした。その結果、通信コストを抑えながらqueueとprefillのtailを短縮した。

## 性能差

| Policy | Mean | p95 | p99 | Max | Redirects |
|---|---:|---:|---:|---:|---:|
| KV handoff | 487.1 ms | 785.6 ms | 1338.6 ms | 1434.3 ms | 24 |
| Multi-candidate | **471.7 ms** | **747.8 ms** | **932.6 ms** | **1267.7 ms** | **18** |
| 改善率 | **3.2%** | **4.8%** | **30.3%** | **11.6%** | **25.0%減** |

平均の改善は15.4 msと小さいが、p99の改善は406.0 msである。したがって、効果は主に
通常requestの一律高速化ではなく、負荷集中によって生じる遅いrequestの回避に現れている。

## Redirect先の違い

| 指標 | KV handoff | Multi-candidate |
|---|---:|---:|
| Redirect数 | 24 | 18 |
| 使用したredirect先GPU数 | 5 | 9 |
| 最多redirect先の件数 | 16 | 5 |
| 最多redirect先への集中率 | 66.7% | 27.8% |

KV handoffでは24件中16件がGPU 5へ送られた。Multi-candidateでは18件が9 GPUへ分散し、
最多でも5件だった。第2近傍だけを見る方式では、同じ時間帯に到着した複数requestが同じ候補を
選びやすい。Multi-candidateは各到着時点で収容可能な候補を横断比較するため、この集中を避けた。

## Request単位の差

300 requestsのうち、最終GPUが異なったのは25件だけだった。

| Request群 | 件数 | Multi - KV handoffの平均TTFT差 | 合計差 |
|---|---:|---:|---:|
| 最終GPUが同じ | 275 | +1.6 ms | +434.6 ms |
| 最終GPUが異なる | 25 | **-201.8 ms** | **-5044.2 ms** |

つまり、Multi-candidateの全体改善は少数のrouting変更から生じている。最終GPUが同じ275件では
わずかに悪化したが、25件の候補変更による5.04秒の改善がそれを上回った。

Routing動作別では、KV handoffがredirectしたがMulti-candidateがlocalに残した7件が平均
515.7 ms改善した。また、両方がredirectした17件も、別の空いているGPUを選ぶことで平均
101.5 ms改善した。一方、KV handoffがlocal、Multi-candidateがredirectとなった1件は
291.7 ms悪化した。個別の誤判断は残るが、全体では候補拡張の便益が大きい。

## TTFT breakdownから見た理由

| Policy | Communication | Router wait | Scheduler queue | Prefill service | Other/staging |
|---|---:|---:|---:|---:|---:|
| KV handoff | 25.4 ms | 0.0 ms | 29.9 ms | 426.5 ms | 5.3 ms |
| Multi-candidate | **19.0 ms** | 0.0 ms | **28.0 ms** | **424.5 ms** | 0.1 ms |

平均では、redirect数が24件から18件へ減ったため通信成分が6.3 ms減り、Scheduler queueと
Prefill serviceもそれぞれ約2 ms減った。大きな差はtailにある。

| Policy | Communication | Router wait | Scheduler queue | Prefill service | Other/staging |
|---|---:|---:|---:|---:|---:|
| KV handoff tail上位1% | 317.3 ms | 0.0 ms | 213.4 ms | 648.4 ms | 230.0 ms |
| Multi-candidate tail上位1% | **211.6 ms** | 0.0 ms | 316.4 ms | **586.5 ms** | **0.9 ms** |

Multi-candidateのtailではScheduler queue単体は103.0 ms増えているが、通信が105.7 ms、
Prefill serviceが61.9 ms、staging等の残差が229.1 ms減り、合計では大幅に改善した。
したがって「常にqueueが短いから」ではなく、重いhandoff先への集中と不要なmigrationを避け、
tailの総コストを下げたと解釈するのが適切である。

## 代表例

- Request 136: KV handoff 1399.8 ms → Multi 422.7 ms。KVはGPU 9へredirectしたが、
  MultiはGPU 5でlocal処理し、通信317.3 msと長いqueue/prefillを回避した。
- Request 263: 1393.3 ms → 712.1 ms。両方redirectだが、GPU 5からGPU 0へ候補を変更し、
  queueを217.3 msから8.1 ms、prefillを856.1 msから386.5 msへ短縮した。
- Request 175: 1434.3 ms → 715.5 ms。GPU 5からGPU 2へ変更し、同様に混雑を回避した。

一方、Request 173は749.5 msから1267.7 msへ悪化した。Multiが選んだGPU 1でqueueとprefillが
予測時より増えたためであり、候補選択後の同時到着や将来負荷までは完全に予測できていない。

## 考察

Dynamic formulaがKV handoffと同一のTTFT分布だったことから、`target_not_admissible`時のlocal固定を
廃止するだけでKV handoff相当までは回復できた。その先のMulti-candidateの改善は、候補数を増やし、
混雑先を避けたことによる。したがって今回の優位性は、精緻な絶対TTFT予測よりも、候補間の相対順位が
概ね正しく、負荷分散に使えたことに依存している。

ただし、この結果は固定APN propagation、1 workload、1 seedでの結果である。第3候補以降の実距離を
workloadが持たないため、距離依存通信を有効にすると候補拡張の通信ペナルティが増える可能性がある。
またrouting変更後は後続requestのscheduler状態も変わるため、request単位差分は完全に独立な因果効果ではない。
次の検証では複数seed、負荷強度、候補別距離を追加し、候補数上限と将来予約を含めて再評価すべきである。
