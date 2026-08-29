# PP2環境におけるKV migrate効果の再検証

## 1. 目的

従来の本郷ワークロード実験では、PP2を導入するとTTFTが大きく改善した一方、
redirectがほぼ、またはまったく発生しなくなった。そのため、PP2とKV migrateを
同時に有効化した構成が良好な結果を示していても、次の二つを分離できていなかった。

1. PP2そのものによる改善
2. PP2環境におけるKV migrateの追加改善

本実験の目的は、PP2でもredirectが発生し、かつredirect先に余剰capacityがある
条件を作り、同じrouting判断のもとでcold redirectとKV migrateを比較することで
ある。

## 2. 結論

局所的なKVメモリhotspotがあり、他のPP2 instanceに余剰KV容量が残っている条件では、
KV migrateはcold redirectに対して明確な追加効果を示した。

| 指標 | PP2 cold | PP2 KV | KV migrateによる改善 |
|---|---:|---:|---:|
| Redirect数 | 92/300 | 92/300 | 同一リクエスト集合 |
| 平均TTFT | 4104.8 ms | 3515.7 ms | 589.1 ms（14.4%） |
| p50 TTFT | 2733.4 ms | 2044.0 ms | 689.4 ms |
| p95 TTFT | 12086.6 ms | 10947.2 ms | 1139.4 ms |
| p99 TTFT | 15509.2 ms | 15509.2 ms | 0 ms |
| Redirect時の平均KV移送時間 | 0 ms | 203.3 ms | KV版の追加コスト |

両方式でredirectされた92件は完全に同一である。この92件に限定したpaired比較でも、
KV migrateにより平均TTFTが325.1 ms改善した。KV移送時間を支払っても、redirect先で
prefixを再計算するより、再利用可能KVを移送する方が速かった。

全300件での平均改善589.1 msがredirectされた92件だけの改善325.1 msより大きいのは、
KV再利用による処理時間短縮が後続のlocalリクエストのqueueも間接的に緩和したためで
ある。一方でp99は改善しておらず、最悪tailを支配する別のボトルネックは残っている。

したがって、本実験では次の運用上の組み合わせ効果を確認した。

> PP2環境でも、局所的なKV容量不足と移送先の余剰capacityが共存する場合、
> KV migrateはcold redirectよりTTFTを改善する。

ただし、統計的な意味での厳密なPP×KV交互作用、すなわち「KV migrateの改善幅が
PP1よりPP2で大きい」ことはまだ確認していない。その判定には、同じワークロードで
`pp1_cold`と`pp1_kv`も実行し、次を計算する必要がある。

```text
(TTFT_pp2_cold - TTFT_pp2_kv)
  - (TTFT_pp1_cold - TTFT_pp1_kv)
```

## 3. 検証に使用したワークロード

### 3.1 基本情報

使用したワークロードは次のファイルである。

```text
experiments/2026-08-09-test-some-workload/workloads/pp2/load_12x_hot50.jsonl
```

| 項目 | 設定 |
|---|---:|
| リクエスト数 | 300 |
| ユーザー数 | 288 |
| セッション数 | 288 |
| 再訪リクエスト数 | 12 |
| 再訪率 | 4% |
| 到着時間幅 | 約2.76秒 |
| 実効到着率 | 約108.8 requests/s |
| 負荷 | 本郷busy hourの12倍 |
| モデル | Meta Llama 3.1 8B |
| 物理GPU | RTX 4090 × 12台 |
| Parallelism | PP2 |
| Logical instance数 | 6 |
| `max_num_seqs` | 32 |
| `max_num_batched_tokens` | 2048 |

### 3.2 リクエスト長とKV再利用量

コンテンツにはShareGPT由来の長い会話を使用している。

| トークン数 | 平均 | p50 | p95 | 最小 | 最大 |
|---|---:|---:|---:|---:|---:|
| Input | 3860 | 3499 | 5844 | 3004 | 7825 |
| Output | 279.6 | 205.5 | 745.2 | 1 | 1663 |
| 再利用可能prefix | 1922.1 | 1744 | 2915.2 | 1488 | 3904 |

再利用可能prefixは各inputのおよそ50%であり、KV block境界への丸めを含む平均比率は
49.8%である。

### 3.3 PP2構成

12台の物理GPUを2台ずつ組み合わせ、6個のlogical instanceとしている。

```text
Logical instance 0: physical GPU 0, 1
Logical instance 1: physical GPU 2, 3
Logical instance 2: physical GPU 4, 5
Logical instance 3: physical GPU 6, 7
Logical instance 4: physical GPU 8, 9
Logical instance 5: physical GPU 10, 11
```

主な通信条件は次のとおりである。

| 項目 | 設定 |
|---|---:|
| PP段間帯域 | 16 GB/s |
| PP段間latency | 20 µs |
| GPU backbone帯域 | 10.7 Gbps |
| APN固定伝搬遅延 | 300.5 µs |
| KV staging帯域 | 33.8 GB/s |
| KV staging latency | 102.9 ns |

## 4. 局所hotspotの設定

元の本郷ワークロードは、リクエストがGPU間でほぼ均等に分布していた。本実験では
KV migrateが利用可能になる必要条件を作るため、logical instance 0へ決定論的な
hotspotを導入した。

| Home logical instance | リクエスト数 | 割合 |
|---|---:|---:|
| Instance 0 | 150 | 50% |
| Instance 1 | 30 | 10% |
| Instance 2 | 30 | 10% |
| Instance 3 | 30 | 10% |
| Instance 4 | 30 | 10% |
| Instance 5 | 30 | 10% |

hotspotの150件は到着時間の前半へ集中させず、時間軸全体に決定論的に分散した。
これにより、次の状態を作った。

- instance 0ではKV予約容量が先に不足する
- 他の5 instanceにはKV容量が残る
- instance 0から他instanceへのredirectが可能になる

今回の92件のredirectは、sequence slot上限ではなく、すべてNPUのKVメモリ容量不足で
発火した。

| Redirect判定時の値 | 観測範囲 |
|---|---:|
| `redirect_capacity_reason` | 全92件が`npu_memory` |
| Running requests | 12〜20 |
| `max_num_seqs` | 32 |
| Slot使用率 | 40.6〜65.6% |
| 追加要求KV容量 | 約201〜474 MB |
| 利用可能KV容量 | 約29 MB |
| Capacity pressure | 1.010〜1.027 |

接続可能数には余裕があったが、リクエストの最大contextまで予約するKV容量が不足し、
home instanceがadmissibleでなくなったことが直接のredirect理由である。

## 5. 比較した方式

### 5.1 PP2 cold

```text
NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE
```

- homeに必要なKV容量がなければredirectする
- 全admissible instanceからcapacity pressure最小の宛先を選ぶ
- redirect先へKVを移送しない
- redirect先でprefixを含むprefillを再計算する

このポリシーは今回の比較用に追加したmatched controlである。

### 5.2 PP2 KV

```text
NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE
```

- homeに必要なKV容量がなければredirectする
- cold版と同じ候補列挙・capacity pressure基準を使う
- 再利用可能prefix KVをredirect先へ移送する
- KV hit分だけredirect先でのprefill計算量を減らす

候補列挙と宛先選択を揃えているため、両方式の主要な差はredirect時のKV移送有無で
ある。従来比較の`NEAREST_MIGRATE`対
`NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`とは異なり、第二近傍だけを見る方式と
全候補を見る方式の違いが混入しない。

## 6. なぜ従来ワークロードではPP2時のKV migrate効果を測れなかったか

### 6.1 PP2でredirectが発生していなかった

従来の300件・均等配置ワークロードでは、PP2にすると各logical instanceの処理能力と
KV容量に余裕が生じ、busy hourからpeak 10xまでredirectは0件だった。

KV migrateはredirectされたリクエストにだけ作用する。redirectが0件なら、KV migrateを
有効化しても実行経路が一度も使われない。そのためPP2+KV構成の改善は、実質的には
PP2単体の改善だった。

### 6.2 到着率だけを上げても空いた移送先が生まれなかった

今回、300件の到着時間軸を12x、15x、20x、30xへ圧縮して確認したが、PP2 coldの
redirectはすべて0件だった。

| 負荷 | Redirect | 平均TTFT | p95 TTFT | p99 TTFT |
|---|---:|---:|---:|---:|
| 12x | 0/300 | 2165.0 ms | 4071.6 ms | 4362.7 ms |
| 15x | 0/300 | 2444.2 ms | 4636.6 ms | 4912.0 ms |
| 20x | 0/300 | 2712.0 ms | 5124.4 ms | 5426.0 ms |
| 30x | 0/300 | 2995.8 ms | 5598.8 ms | 5900.0 ms |

TTFTは悪化しているが、routing gateは単なる待ち時間ではなく、主にsequence slotと
最大contextまで含むKV予約可能性を判定する。均等負荷では全instanceが同時に混雑する
ため、homeが処理不能になる頃にはredirect先にも余剰capacityがない。したがって、
全体負荷の増加だけではKV migrateが使える状況にならなかった。

### 6.3 300件という総数がPP2全体のcapacityに対して小さかった

PP2には6 logical instanceがあり、デフォルトの`max_num_seqs=128`ではsequence slotの
総数は768である。300件では、すべてが同時に接続してもslot総数を超えない。

また、`max_num_seqs=32`へ下げた均等配置プローブでもredirectは0件だった。この条件では
TTFTだけが悪化し、p99は11.4秒に達した。全instanceが均等に混雑していたため、localで
待たせることはあっても、有効なredirect先が生まれなかった。

### 6.4 Cold対KVでrouting方式自体が揃っていなかった

従来の代表的な比較は次の組み合わせだった。

- Cold相当: `NEAREST_MIGRATE`
- KV版: `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`

前者は基本的に第二近傍へredirectし、後者は全候補からcapacity pressure最小を選ぶ。
したがって観測差には、KV移送の有無だけでなく候補範囲と宛先選択方式の差も含まれて
いた。今回matched cold controlを追加したことで、この交絡を除いた。

## 7. 解釈上の制約

今回の結果は、KV migrateが効果を持ち得ることを確認する機構プローブである。次の
一般的な主張にはまだ使用できない。

- 現実の本郷で50%のリクエストが1 instanceへ集中する
- `max_num_seqs=32`が実運用に適した設定である
- 均等な全体飽和でもKV migrateが有効である
- 複数seedでも14.4%の改善が再現する
- Network contentionを考慮しても改善が残る
- PP1よりPP2の方がKV migrateの改善幅が大きい

また、hotspot生成ではlogical home IDを変更したが、元のユーザー座標、GPU座標、
一部の距離・uplink latencyフィールドは再計算していない。cold/KV間では共通なので
差分比較への影響は限定的と考えられるが、地理的に忠実な本郷評価へ用いる場合は、
配置と通信latencyを整合的に再生成する必要がある。

## 8. 次の検証

1. 同じhotspot条件で`pp1_cold`と`pp1_kv`を実行し、正式なPP×KV交互作用を算出する
2. Hotspot比率を事前に定めた複数点で検証し、50%固有の結果でないことを確認する
3. 複数seedでredirect率とTTFT改善の信頼区間を求める
4. `max_num_seqs=128`のまま局所KV pressureが発生する、より大規模なワークロードで
   再確認する
5. Network contentionを有効化し、KV移送がネットワークを圧迫する条件を評価する
6. ユーザー位置と通信latencyを含めて整合的なhotspotワークロードを再生成する

