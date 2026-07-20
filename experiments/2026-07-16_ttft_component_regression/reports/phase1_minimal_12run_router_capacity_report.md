# Phase 1最小12 runs：Router capacityとTTFT支配要因解析

## 1. 目的

全162 runsを実行する前に、Router queueの支配要因を特定できるか確認するため、capacity regimeを代表する4 workloadを選び、A/B/Cの3 policyで合計12 runsを実行した。

本解析では、2026-07-19に追加した全request共通のRouter capacity snapshotを使用する。特に、最初のrouting試行時点を表す`router_initial_*`と、最終的なrouting決定時点を表す`router_decision_*`を区別する。

## 2. 実験条件

| Workload | 位置付け |
|---|---|
| input 4000 / 2.5 req/s / reuse 0% | 低負荷・capacity余裕あり |
| input 6000 / 3.33 req/s / reuse 0% | Capacity境界 |
| input 8000 / 2.5 req/s / reuse 0% | 長入力・高KV圧力 |
| input 8000 / 2.5 req/s / reuse 50% | 長入力・reuseによる負荷緩和 |

各workloadは300 requests、seed 1である。Policyは次のように対応する。

| 表記 | Policy | 動作 |
|---|---|---|
| A | `NEAREST_KV` | Home GPUでcapacity解放を待つ |
| B | `NEAREST_MIGRATE` | Capacity不足時にredirectするが、redirect先へKVを移さない |
| C | `NEAREST_MIGRATE_KV` | Capacity不足時にredirectし、再利用KVも移す |

全12 runs、3,600 requestsが正常に完了した。`router_initial_required_kv_bytes`、`router_initial_available_kv_bytes`、`router_initial_projected_active_kv_bytes`、waiting/running数、capacity pressureなどの主要な新規列は3,600件すべてで記録されていた。

## 3. E2E TTFT結果

| Workload | Policy | Mean E2E | p50 | p95 | p99 | Mean Router queue |
|---|---|---:|---:|---:|---:|---:|
| input4000 / 2.5 / reuse0 | A | 504 ms | 469 ms | 740 ms | 1,139 ms | 0 ms |
| | B | 504 ms | 469 ms | 740 ms | 1,139 ms | 0 ms |
| | C | 504 ms | 469 ms | 740 ms | 1,139 ms | 0 ms |
| input6000 / 3.33 / reuse0 | A | 2,783 ms | 754 ms | 19,292 ms | 26,126 ms | 1,952 ms |
| | B | 1,241 ms | 755 ms | 2,355 ms | 11,455 ms | 390 ms |
| | C | 1,241 ms | 755 ms | 2,355 ms | 11,455 ms | 390 ms |
| input8000 / 2.5 / reuse0 | A | 3,834 ms | 1,047 ms | 19,449 ms | 39,532 ms | 2,716 ms |
| | B | 2,262 ms | 1,045 ms | 10,359 ms | 13,884 ms | 1,117 ms |
| | C | 2,262 ms | 1,045 ms | 10,359 ms | 13,884 ms | 1,117 ms |
| input8000 / 2.5 / reuse50 | A | 1,877 ms | 554 ms | 10,767 ms | 24,234 ms | 1,293 ms |
| | B | 1,007 ms | 554 ms | 3,046 ms | 10,161 ms | 365 ms |
| | C | 868 ms | 554 ms | 1,515 ms | 9,684 ms | 237 ms |

## 4. Router queue発生の支配要因

### 4.1 Capacity pressureの定義

Router初回判定時のKV capacity pressureを次のように定義した。

```text
capacity pressure
  = (projected active KV bytes + required KV bytes)
    / KV budget bytes
```

`capacity pressure <= 1`ならKV budget上は候補requestを収容可能であり、1を超えると収容後の予約KV量がbudgetを超える。

### 4.2 Router待ちあり・なしの比較

| Router初期状態 | 待ちなし | Router待ちあり |
|---|---:|---:|
| Home GPU capacity pressure | 0.515 | 1.060 |
| Sequence slot pressure | 0.042 | 0.076 |
| 全候補GPUのrunning requests | 46.5 | 61.4 |
| 収容可能な候補GPU数 | 8.92 | 6.70 |

最初にcapacity判定でblockされた521 requestsは、すべて`npu_memory`が理由だった。`sequence_full`は発生していない。

この結果から、今回の4 workloadsでRouter queueの発生を決める第一要因は、`max_num_seqs`ではなくKV容量の逼迫であると判断できる。

### 4.3 Home GPUの収容可能性

初期状態の`router_initial_admissible`とRouter待ちには次の関係があった。

```text
Home GPUが収容可能:
  Router待ち発生率 0%

Home GPUが収容不可能:
  Router待ち発生率 72.7%
```

Home GPUが収容不可能でも27.3%が待たなかったのは、B/Cが別GPUへ即時redirectできたためである。

処理は次の三つへ分類できる。

```text
Home GPUに容量あり
  -> そのまま受け入れ
  -> Router待ちなし

Home GPUに容量なし、redirect先に容量あり
  -> 即時redirect
  -> Router待ちなし

Home GPUにもredirect先にも容量なし
  -> Capacity解放待ち
  -> Router待ち発生
```

## 5. Aに対してB/Cが改善する理由

### 5.1 Input 6000、reuse 0%

Home GPUでblockされたrequestだけを比較した。

| Policy | Block件数 | Redirect率 | Router待ち率 | 正値Router待ち平均 |
|---|---:|---:|---:|---:|
| A | 47 | 0% | 100% | 12.46秒 |
| B | 48 | 75% | 52% | 4.67秒 |
| C | 48 | 75% | 52% | 4.67秒 |

AはHome GPUが空くまで待つ必要がある。B/Cはblockされたrequestの75%を別GPUへredirectできるため、全request平均のRouter queueが1,952 msから390 msへ減少した。

E2E meanは2,783 msから1,241 msへ約55%短縮し、p95は19,292 msから2,355 msへ約88%短縮した。

このworkloadではreuseが0%なので、BとCの結果は完全に一致した。移す再利用KVが存在しない場合、CのKV handoffは発生しない。

### 5.2 Input 8000、reuse 0%

Input 8000では必要KV量が増えるためcapacity pressureが1へ到達しやすく、AのRouter queueはさらに大きくなった。

```text
A mean Router queue:   2,716 ms
B/C mean Router queue: 1,117 ms

A p99 E2E:   39.5秒
B/C p99 E2E: 13.9秒
```

Reuse 0%ではここでもBとCは一致する。改善はKV handoffではなく、redirectによるcapacity wait回避によるものである。

## 6. Reuse 50%におけるBとCの差

Input 8000、reuse 50%ではBとCの差が現れた。

| Component | A | B | C |
|---|---:|---:|---:|
| Mean E2E TTFT | 1,877 ms | 1,007 ms | 868 ms |
| p95 E2E TTFT | 10,767 ms | 3,046 ms | 1,515 ms |
| Mean Router queue | 1,293 ms | 365 ms | 237 ms |
| Mean Scheduler queue | 25 ms | 32 ms | 27 ms |
| Mean Compute | 559 ms | 610 ms | 562 ms |
| Mean KV transfer | 0 ms | 0 ms | 41 ms |

Bはredirectしたrequestでreuseを失うため、全request平均の実現reuse量は3,600 tokensとなり、computeは610 msへ増加した。CはKVを移動するため平均41 msの転送コストを払うが、4,000 tokensのreuseを維持し、computeを562 msに抑えた。

BからCへの変化をcomponentごとに示す。

```text
KV transfer:
  Cが約41 ms不利

Compute:
  Cが約48 ms有利

Router queue:
  Cが約127 ms有利

E2E TTFT:
  Cが約139 ms有利
```

KV transfer単体は追加コストである。しかし、reuse維持によるcompute削減と、処理完了時刻・KV解放時刻の変化によるRouter queue削減を合わせると、CがBより有利になる。

## 7. Scheduler queueの位置付け

Scheduler queueは概ね25～89 msであり、高負荷条件のRouter queueと比較すると小さい。

Redirectによって移動先schedulerのqueueが増える場合は確認された。

```text
Input 6000 / reuse 0%:
  A   66 ms
  B/C 80 ms

Input 8000 / reuse 0%:
  A   75 ms
  B/C 89 ms
```

B/Cは移動先GPUのscheduler負荷を若干増やすが、Router queueを1秒以上削減する効果の方が大きいため、E2E TTFTは改善する。

## 8. 現時点での支配要因

| Component・現象 | 支配要因 |
|---|---|
| Router queue発生 | Home GPUの初期KV capacity pressure |
| 即時redirectの可否 | Redirect先GPUにrequired KVを収容できるか |
| Router queue長 | 先行requestの完了と、その後のKV解放タイミング |
| Compute | 実際のuncached input tokens |
| Scheduler queue | 移動先を含む局所的なbatch・scheduler状態 |
| KV transfer | KV移動有無と移動bytes |
| E2E TTFT | Input 4000ではcompute、6000～8000ではRouter queue |

初期capacity pressureと正値Router queue長のSpearman相関は`rho = 0.121`と弱かった。一方、初期Home GPUが収容可能なrequestではRouter待ちが一度も発生していない。

したがって、初期KV capacity pressureは「待ちが発生するか」を強く決めるが、「何秒待つか」はその後のrequest完了、KV解放、redirect先の状態変化に左右される。

`router_capacity_retry_count`と正値Router queue長の相関は高いが、retry回数は待ち時間の経過後に確定する結果変数である。支配要因を説明する回帰モデルの入力には使用せず、simulation動作の診断値としてのみ使用する。

## 9. 制約と追加ログ候補

今回のsnapshotでは、Home GPUの初期状態と候補GPU群の集約状態、最終移動先GPUのdecision時状態を保存している。一方、B/Cが実際に検討するsecond-nearest GPUについて、最初のrouting試行時点の個別snapshotは保存していない。

そのため、即時redirectできたrequestとcapacity waitになったrequestの違いをrequest単位で厳密に説明するには、次の`router_initial_target_*`列を追加する価値がある。

- Target GPU ID
- Target required/available/projected KV bytes
- Target capacity/slot pressure
- Target waiting/running requests
- Target admissible flag

ただし、現在のデータだけでも「Home GPUのKV capacity pressureがblock発生を決め、redirect可能性がRouter queueを削減する」という第一段階の結論は支持される。

## 10. 次の実験

今回は各条件seed 1のみなので、傾向の再現性や信頼区間は評価できない。全162 runsへ直ちに拡大する必要はなく、まず今回の4条件についてseed 2とseed 3を追加する。

```text
4 conditions x 2 additional seeds x 3 policies = 24 additional runs
```

合計36 runsになれば、同一condition内のseed変動を含め、Router queue発生率、component平均、p95/p99、group ablation importanceへ信頼区間を付けられる。

## 11. 結論

今回の12 runsから、Router queue発生の直接的な支配要因はKV capacity pressureであり、sequence slot不足ではないことが確認できた。

低負荷のinput 4000ではA/B/Cに差がない。Input 6000～8000ではKV capacity境界を超えるrequestが発生し、redirect可能なB/CがAの長いRouter waitを回避する。Reuse 0%ではB/Cは一致するが、reuse 50%ではCがKV転送コストを上回るreuse維持効果を得て、BよりMean E2Eを139 ms、p95を約1.53秒短縮した。

現時点のボトルネック構造は次のようにまとめられる。

```text
短いinput・低負荷:
  Compute支配

長いinput・capacity境界超過:
  Router KV-capacity wait支配

Redirectあり:
  Router waitを削減する代わりに、移動先scheduler負荷がわずかに増加

Redirect + KV handoff + reuseあり:
  KV transferコストを払うが、reuse維持とcapacity状態改善によりE2Eを短縮
```
