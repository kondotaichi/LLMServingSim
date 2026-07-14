# NEAREST系3方式のワークロード感度実験計画

## 1. 目的

次の3方式について、どのワークロード条件で優劣が変わるかを調べる。

- A: `NEAREST_KV`（最寄りGPUでwait、local KV再利用）
- B: `NEAREST_MIGRATE`（request forward、redirect先はcold prefill）
- C: `NEAREST_MIGRATE_KV`（request forward、KV handoff）

現在のPrompt 6000実験では、Cがmeanおよびtail latencyで最良だった。しかし、平均
E2E TTFTの主要成分はQueue / waitであり、Cの有効性が高負荷条件に依存している可能性が
ある。本計画では、負荷、地域偏り、prompt長、KV再利用率、出力長、burst性を変化させ、
Cの有利・不利となる境界を求める。

## 2. 現在の基準点

| 指標 | A: NEAREST_KV | B: NEAREST_MIGRATE | C: NEAREST_MIGRATE_KV |
|---|---:|---:|---:|
| Mean E2E TTFT | 4,639.7 ms | 3,040.9 ms | 2,613.8 ms |
| Queue / wait | 4,213.8 ms | 2,526.4 ms | 2,103.5 ms |
| Compute / prefill | 425.9 ms | 514.3 ms | 444.7 ms |
| KV transfer | 0 ms | 0 ms | 65.5 ms（全件平均） |
| Redirect件数 | 0 | 64 | 62 |

Cのredirect 1件あたりKV handoff時間は約316.7 msである。したがって、home GPUで回避
できる待ち時間とcold prefill回避効果の合計が約316.7 msを上回ると、Cが有利になりやすい。

概念的な損益条件は次で表せる。

```text
Cが有利:
  avoided_home_wait
  + avoided_cold_prefill
  > KV_handoff_time
  + additional_target_wait
```

## 3. 最優先: 到着負荷スイープ

### 3.1 Queueを半分にする方法

リクエスト数を減らすと標本数も減るため、300件は維持し、全arrival timeを同じ倍率で
引き延ばす。現在は約60秒に300件、平均5 req/sである。

| ケース | Arrival倍率 | 到着期間 | 平均到着率 | 目的 |
|---|---:|---:|---:|---|
| L0 | 1.0 | 60 s | 5.00 req/s | 現在の高負荷基準 |
| L1 | 1.25 | 75 s | 4.00 req/s | 軽度緩和 |
| L2 | 1.5 | 90 s | 3.33 req/s | Queue半減候補 |
| L3 | 2.0 | 120 s | 2.50 req/s | Queue 1/4〜低負荷候補 |
| L4 | 3.0 | 180 s | 1.67 req/s | ほぼQueueなし |

Queueは利用率に対して非線形に増えるため、arrivalを2倍に引き延ばしてもQueueが厳密に
半分になるとは限らない。まずAだけをL1〜L4で実行し、平均Queue / waitが次の範囲になる
ケースを選ぶ。

- 約2.1秒: 現在の半分
- 約1.0秒: 現在の1/4
- 約0.2〜0.4秒: KV handoff時間と同程度
- 0.1秒未満: 低負荷

選んだ3点についてA/B/Cを比較する。特に0.2〜0.4秒付近がCとAの損益分岐を調べる上で
重要である。Queueを半分にした約2.1秒では、handoff時間316.7 msよりまだ十分大きいため、
Cが引き続き有利な可能性が高い。

### 3.2 到着負荷で予想される結果

| 負荷 | 予想 |
|---|---|
| 低負荷 | Aが有利。redirectで支払う通信時間を回収できない |
| 中負荷 | AとCの損益分岐。requestごとのhome待ち時間に依存 |
| 高負荷 | Cがmean/tailで有利。Bはcold prefillによりCより不利 |
| 極端な過負荷 | 第2近傍も満杯になり、B/Cの効果が飽和する |

## 4. 地域負荷偏り

平均到着率が同じでも、セル間の偏りによってmigration価値は変わる。

| ケース | 地域分布 | 目的 |
|---|---|---|
| S0 | 10セル均等 | hotspotがない場合の通信オーバーヘッド確認 |
| S1 | 現在のWildChat比率 | 実データ基準 |
| S2 | 最大セルを現在の1.5倍 | 中程度hotspot |
| S3 | 1セルへ40〜50%集中 | 強いhotspot |
| S4 | 隣接2セルへ同時集中 | 第2近傍も混雑する条件 |
| S5 | hotspotが時間とともに移動 | KV配置と負荷の時間変化への追従性 |

Cが最も有利なのは、1つのhome GPUが混雑し、第2近傍GPUに余裕があるS2/S3である。
S0ではAが有利になりやすい。S4ではredirect先も満杯になるため、B/Cの効果が小さくなる。

## 5. Prompt長とKV再利用率

Cの価値は「cold prefillをどれだけ省けるか」と「KVを何byte送るか」の両方で変わる。

### 5.1 Prompt長

```text
1,000 / 3,000 / 6,000 / 12,000 tokens
```

- 短prompt: cold prefillが安く、KV handoffの価値が小さい
- 長prompt: cold prefill回避効果が増えるが、KV転送量も増える
- 長prompt: GPUメモリ圧力が高まり、redirect自体も増える

### 5.2 KV再利用率

```text
0% / 25% / 50% / 75% / 90%
```

- 0%: CはBと同等であるべきで、余分なKV転送は発生しない
- 低再利用率: 転送対象が小さいが、cold prefill削減効果も小さい
- 高再利用率: prefill削減効果が大きいが、転送量も増える

最初から4×5の全組合せを実行すると高コストなので、次の順で絞り込む。

1. Prompt 3,000 / 6,000 / 12,000、reuse 25% / 50% / 75%の3×3
2. CとBの差が符号反転する領域を確認
3. 境界付近だけ細かいreuse率を追加

## 6. 出力長とactive KV保持時間

Prompt長が同じでも、出力長が長いとrequestがGPUに長く滞在し、decode KVが増える。

| ケース | 出力長 | 目的 |
|---|---|---|
| O0 | 1〜32 tokens | prefill中心、短時間保持 |
| O1 | ShareGPT分布 | 現在の基準 |
| O2 | 512 tokens固定 | decode負荷増加 |
| O3 | 1,024〜2,048 tokens | 長時間KV保持、強いメモリ圧力 |
| O4 | 短・長混在のheavy tail | head-of-lineとrequest-size不均一性 |

長出力ではhome GPUの容量解放が遅くなるためCが有利になりやすい。一方、全GPUが同時に
長decodeを抱える場合はtarget側の余裕もなくなり、migration効果が飽和する可能性がある。

## 7. Burst性

平均到着率を固定し、時間方向の分布だけを変える。

| ケース | 到着パターン |
|---|---|
| B0 | 等間隔 |
| B1 | Poisson |
| B2 | 現在のWildChat時系列 |
| B3 | 20ユーザ同時アクセス |
| B4 | 5秒ごとのmicroburst |

平均負荷が低くてもburst時には一時的なメモリ不足が発生するため、CはB3/B4で有利になる
可能性がある。ただし、短いburstでhome GPUがすぐ空く場合は、316.7 msのhandoffより
waitの方が安い可能性もある。

## 8. Request長の不均一性

同じ平均token数でも、固定長とheavy-tail分布では結果が異なる。

- 全件同一長
- ShareGPT実分布
- 80%短prompt + 20%長prompt
- 95%短prompt + 5%極端な長prompt

現在のpolicyはrequestごとのhandoff損益を予測せず、容量条件だけでredirectする。大きなKVを
持つ長promptを無条件にhandoffすると、短いhome waitより転送時間が長くなる可能性がある。
このケースは将来の選択的migration policyを設計する上で重要である。

## 9. ネットワーク感度

これはワークロードそのものではないが、Cの損益境界に直接影響するため必要である。

```text
APN帯域: 1 / 5 / 10.7 / 25 / 100 Gbit/s
RTT: 0.1 / 0.601 / 2 / 10 ms
CPU staging帯域: 16 / 33.8 / 64 GB/s
```

- 低帯域・高遅延: AまたはBが有利になりやすい
- 高帯域: Cのhandoffコストが下がり、有利領域が広がる
- CPU stagingがボトルネック: APNを高速化してもCが改善しない

## 10. KV存在確率と配置

現在は再利用可能KVがhome GPUに必ず存在すると仮定している。実運用に近づけるには次も必要で
ある。

- KV存在確率: 0% / 25% / 50% / 75% / 100%
- KVがhomeではなく別GPUにあるケース
- Cache evictionにより一部blockだけ残るケース
- 複数ユーザで共通prefixを共有するケース
- Multi-turn sessionのthink timeを含むケース

Cは再利用可能KVが実際に存在するときだけcold prefillを回避できる。KV missが多い場合、
Bとの差は小さくなる。

## 11. 評価指標

各ケースで最低限、次を記録する。

### Latency

- E2E TTFT: mean / p50 / p95 / max
- TPOT: mean / p50 / p95 / max
- E2E completion latency: mean / p50 / p95 / max
- RedirectされたrequestだけのE2E TTFT

### Breakdown

- Router capacity waitを含むQueue / wait
- Prefill compute
- KV handoff
- Request forward / RTT / other communication
- Decode after TTFT

### System

- Request throughput
- Output token throughput
- Makespan
- Redirect件数・率
- GPU別処理件数と標準偏差
- 最大queue長
- GPU最大メモリ使用量
- 総KV migration bytes

### Policy損益

同じ`request_id`を方式間で対応させ、次を求める。

```text
delta_ttft_C_vs_A = E2E_TTFT_C - E2E_TTFT_A
delta_ttft_C_vs_B = E2E_TTFT_C - E2E_TTFT_B
```

request length、home cell、redirect有無、handoff bytesごとにdeltaを分析すると、Cが有利に
なる条件をrequest単位で特定できる。

## 12. 実行コストを抑えた推奨順序

1回50〜90分かかるため、最初から全組合せを実行しない。

### Phase 1: Queue損益分岐

1. Arrival 75 / 90 / 120 / 180秒をAだけでpilot
2. Queueが現在の1/2、1/4、約0.3秒となる3点を選択
3. 選択した3点でA/B/Cを実行

### Phase 2: KV handoff価値

1. Phase 1の中負荷点を固定
2. Prompt 3k / 6k / 12k × reuse 25% / 50% / 75%
3. まずB/Cだけを比較
4. 境界ケースにAを追加

### Phase 3: 地域偏りとburst

1. Uniform / current / single hotspot / adjacent hotspot
2. Smooth / Poisson / simultaneous burst
3. Cのredirect先が空いている場合と混雑する場合を比較

### Phase 4: Robustness

1. 出力長
2. Request長heavy tail
3. APN・CPU staging感度
4. KV存在確率

## 13. 予想される有利・不利条件

### C: NEAREST_MIGRATE_KVが有利

- Home GPUのQueue / waitがKV handoff時間より長い
- Promptが長く、cold prefillが高価
- KV再利用率が高い
- 第2近傍GPUに余裕がある
- 地域負荷にhotspotがある
- APNとCPU stagingが十分高速
- 長出力によりhome GPUのKV解放が遅い
- tail latencyを重視する

### Cが不利

- 低負荷でhome GPUの待ち時間が短い
- Promptが短い、またはKV再利用率が低い
- APN帯域が低い、CPU stagingが遅い
- 第2近傍GPUも同時に混雑している
- KVが既にevictされている、または存在確率が低い
- 短いburstで、待てばすぐhome GPUが空く
- Throughputだけを重視し、cold prefillを並列実行できる

## 14. 最初に実施する実験

最初の追加実験はarrival time倍率1.5、すなわち300件を約90秒へ引き延ばすケースを推奨
する。これは現在の5 req/sを3.33 req/sへ下げ、Aの実測throughput 2.63 req/sとB/Cの
3.18〜3.36 req/sの間に置くためである。

この点では、Aはまだ過負荷寄りだがB/Cは処理能力内に近く、Queue半減と方式差の縮小が
期待できる。90秒でQueueが目標から外れた場合、75秒または120秒へ調整する。
