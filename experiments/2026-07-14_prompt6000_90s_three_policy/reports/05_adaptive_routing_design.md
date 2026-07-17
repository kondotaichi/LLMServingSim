# 現行redirect routingの課題と適応型routing設計案

作成日: 2026-07-15

## 1. 目的

本資料は、Prompt 6000・90秒・3方式比較実験で確認されたredirect routingの課題を整理し、次に実装する適応型routingの設計方針をまとめる。

対象方式:

- A: `NEAREST_KV` — 最寄りGPUで容量が空くまで待機
- B: `NEAREST_MIGRATE` — 第2近傍GPUへredirectし、cold prefill
- C: `NEAREST_MIGRATE_KV` — 第2近傍GPUへredirectし、Prefix KVをhandoff

主な関連分析:

- [3方式の詳細比較](01_three_policy_analysis.md)
- [容量ベースredirectと最寄りGPU待機の比較](03_redirect_vs_wait_local_analysis.md)
- [Redirect先集中とScheduler queueへの影響](04_redirect_concentration_analysis.md)

## 2. 現行アルゴリズム

Routerはrequest到着時に最寄りGPUと第2近傍GPUの容量を確認する。

```text
if home GPUに容量がある:
    home GPUで実行
elif second-nearest GPUに容量がある:
    second-nearest GPUへredirect
else:
    Router pending queueで待ち、後で再評価
```

再評価時もhome GPUを優先する。

```text
if home GPUが先に空いた:
    home GPUで実行、rerouted=0
elif second-nearest GPUが先に空いた:
    redirect、rerouted=1
else:
    待機を継続
```

### 2.1 容量判定

GPUに容量があるためには、次の両方を満たす必要がある。

```text
inflight request数 < max_num_seqs
AND
candidateの最大context KV <= 利用可能KV容量
```

KV容量は、waiting/inflight requestが将来必要とする最大context KVをblock単位で予約して評価する。これはdecode中の将来的なNPU memory不足を防ぐ安全なadmission判定である。

### 2.2 BとCの違い

Routing判断はB/Cで同じであり、redirect後の処理だけが異なる。

```text
B: requestだけを移動し、移動先でcold prefill
C: requestとPrefix KVを移動し、残りだけprefill
```

## 3. 実験で確認された効果

90秒版の全体結果:

| 指標 | A | B | C |
|---|---:|---:|---:|
| 平均E2E TTFT | 2004.1 ms | 518.5 ms | **487.1 ms** |
| p95 E2E TTFT | 14021.0 ms | 917.9 ms | **785.6 ms** |
| 最大E2E TTFT | 24548.4 ms | 2575.1 ms | **1434.3 ms** |
| Redirect | 0 | 24 | 24 |

容量ベースredirectはGPU 4のhotspotを回避し、Aの長いRouter capacity waitを大幅に削減した。したがってmigration自体は有効である。

一方、現在の判断は「置けるか」だけを見ており、「どこで実行すれば最も早いか」は見ていない。

## 4. 課題1: 容量があることと、すぐ実行できることが同一視されている

Routerの`has_capacity=True`が保証するのは次である。

```text
Requestの最大context KVを安全に保存できる
```

保証しないもの:

- 現在実行中batchの終了時刻
- 次のbatchへ入れるか
- `max_num_batched_tokens`の残量
- Chunked prefillの残りchunk
- Active decode request数
- Scheduler waiting queue

そのため、KV容量を確保できてもScheduler queueが発生する。

90秒版Cでは、redirect 24件のScheduler queue平均は71.5 ms、non-redirect 276件は26.3 msだった。

```text
KV capacity = 保存可能性
Scheduler capacity = 実行可能性
```

この2つを分離して予測する必要がある。

## 5. 課題2: Redirectせずlocalで待つ方が速いrequestが存在する

Cのredirect 24件について、同じrequest IDのAと比較した。

| 結果 | 件数 | Redirect中の割合 |
|---|---:|---:|
| CがAより速い | 17 | 70.8% |
| Aで待つ方が速い | **7** | **29.2%** |

Aで待つ方が速かった7件では、Cが平均517.2 ms、最大977.1 ms悪化した。

平均breakdown:

| 成分 | Aで待機 | Cでhandoff |
|---|---:|---:|
| Queue / wait | **72.9 ms** | 175.6 ms |
| KV transfer | 0.0 ms | 316.7 ms |
| Prefill | **420.2 ms** | 517.5 ms |
| E2E TTFT | **493.1 ms** | 1010.4 ms |

最寄りGPUは現在KV容量不足でも、短時間で容量が解放される場合がある。現行routerはその解放時刻を予測せず、第2近傍に現在容量があれば即座にredirectする。

## 6. 課題3: Redirect先が第2近傍GPUに固定されている

現在は全GPUを比較せず、各requestの第2近傍GPUだけを候補にする。

90秒版ではB/Cともredirect 24件中16件、66.7%がGPU 5へ集中した。

```text
Home GPU 4/6の容量不足
    ↓
多くのrequestの第2近傍がGPU 5
    ↓
GPU 5へ集中
```

CのGPU 5向けredirectでは、Scheduler queue平均が63.5 msだった。GPU 5のnative request平均36.9 msより26.6 ms長い。

一方、request 263、265、267の判断時点では、GPU 0や2のactive request数がGPU 5より少なかった。KV容量が十分なら、距離が少し遠くても別GPUの方が速かった可能性がある。

## 7. 課題4: 転送中requestの予約が不十分

Routerはredirect決定時にtargetの現在容量を確認し、target到着時に再確認する。この再検査により、KV容量を超えて無制限にadmitすることはない。

ただし、最初の確認からtarget到着までの間、転送中requestを将来負荷として十分に予約していない。複数requestが同じ見かけ上の空き容量を見て、同じtargetへ向かう可能性がある。

90秒版Cでは、request 263、265、267が約0.73秒以内にGPU 5へ到着した。

| Request | Scheduler queue | E2E TTFT |
|---:|---:|---:|
| 263 | 217.3 ms | 1393.3 ms |
| 265 | 226.6 ms | 1141.7 ms |
| 267 | 345.8 ms | 1338.0 ms |

後続ほどScheduler queueが増えている。この3件だけでCのredirect Scheduler queue総量の46.0%を占めた。

## 8. 課題5: Request単体の最適化とクラスタ全体の最適化が異なる

60秒版ではCのredirect 62件中34件で、独立run上はAの方が対象request自身のTTFTが短かった。一方、Cの全体平均TTFTはAより約2.03秒短い。

これは、1件をlocalに残すとそのrequest自身は速くても、hotspot GPUのKVを長く占有し、後続requestを悪化させる可能性があるためである。

したがって、次の単純な選択だけでは不十分である。

```text
対象request自身の予測TTFTが最小のGPUを選ぶ
```

後続requestへのexternalityも評価する必要がある。

## 9. 課題6: Router waitが直接計測されていない

現在のグラフの`Router queue`はCSVの直接計測列ではなく、次の残差である。

```text
Router queue residual =
    E2E TTFT
    - communication
    - Scheduler queue
    - prefill service
```

この残差には次が混在する。

- Home/secondの両方が満杯だったcapacity retry
- Redirect後にtargetが満杯になった待ち
- 時刻更新・丸めによる微小差

適応型routerの予測精度を検証するには、Router状態を明示的に計測する必要がある。

## 10. 解決策: 予測TTFTに基づくA/B/C適応選択

Requestごとに少なくとも3つの候補を比較する。

```text
T_local =
    predicted_local_capacity_release_wait
    + predicted_local_scheduler_queue
    + predicted_cached_prefill
    + request_communication

T_cold_migrate(gpu) =
    request_transfer(gpu)
    + predicted_target_capacity_wait(gpu)
    + predicted_target_scheduler_queue(gpu)
    + predicted_cold_prefill(gpu)
    + response_communication(gpu)

T_kv_handoff(gpu) =
    request_transfer(gpu)
    + predicted_kv_transfer(gpu)
    + predicted_target_capacity_wait(gpu)
    + predicted_target_scheduler_queue(gpu)
    + predicted_cached_prefill(gpu)
    + response_communication(gpu)
```

基本選択:

```text
route = argmin(
    T_local,
    T_cold_migrate(candidate GPUs),
    T_kv_handoff(candidate GPUs),
)
```

現行A/B/Cを固定policyとして選ぶのではなく、requestごとに切り替える。

## 11. Candidate GPUの選び方

第2近傍固定をやめ、近傍上位K台または全GPUからcandidateを作る。

推奨する段階的構成:

1. Home + 第2近傍
2. Home + 近傍上位3台
3. 同一cluster内全GPU
4. Network costでpruneした候補集合

候補数を増やすほど最適化余地は広がるが、状態収集と予測計算の負荷も増える。

## 12. 必要な予測状態

GPUごとに次をrouterへ公開する。

### Memory

- KV budget
- Active requestの予約KV
- Evict可能prefix KV
- 次回KV解放予測時刻
- 転送中requestの予約KV

### Scheduler

- 現在batchの予測終了時刻
- Waiting request数
- Waiting prefill tokens
- Active decode request数
- Remaining decode tokens
- 次iterationのtoken budget予測
- Chunked prefillの残りchunk

### Network

- Request転送量
- KV転送量
- 実効帯域
- 転送中request数
- Network queue予測

## 13. Atomic reservation

Candidateを選んだ時点でtargetへ予約を置く。

```text
reservation = {
    request_id,
    reserved_kv_bytes,
    expected_prefill_tokens,
    expected_arrival_time,
    expiration_time,
}
```

後続routing判断は、すでに転送中のreservationをtarget負荷へ含める。転送失敗やtimeout時にはreservationを解放する。

これにより、複数requestが同じ空き容量を重複して見積もることを防ぐ。

## 14. Externalityを含むscore

高負荷時には、対象request自身のTTFTだけでなく、既存・後続requestへの影響も加える。

```text
routing_score =
    predicted_request_ttft
    + lambda * predicted_added_wait_to_other_requests
    + uncertainty_margin
    + concentration_penalty
```

初期実装では`lambda=0`でrequest単位予測を検証し、その後クラスタ全体のSLO違反増分を近似する。

## 15. Request 263、265、267から見える理想判断

| Request | A | C→GPU 5 | 解釈 | 理想候補 |
|---:|---:|---:|---|---|
| 263 | 4544.4 ms | **1393.3 ms** | Redirect自体は有効 | GPU 5以外も比較 |
| 265 | **797.6 ms** | 1141.7 ms | Redirect自体が不利 | Homeで待機 |
| 267 | 4821.5 ms | **1338.0 ms** | Redirect自体は有効 | GPU 5以外も比較 |

適応型routerは、同じburst内でも263/267はredirectし、265はhomeに残す必要がある。単純な集中回避だけでなく、requestごとのlocal解放待ち予測が必要である。

## 16. 実装ロードマップ

### Phase 1: 計測

次のtimestamp/counterをCSVへ追加する。

- `router_enqueue_time_ns`
- `router_first_check_time_ns`
- `router_admit_time_ns`
- `router_capacity_wait_ns`
- `target_capacity_wait_ns`
- `capacity_retry_count`
- `scheduler_enqueue_time_ns`
- `predicted_*_ttft_ns`
- `selected_route_reason`

### Phase 2: Oracle replay

Redirect判断直前の状態をcheckpointし、同一状態から次を分岐実行する。

1. Homeで待機
2. Candidateへcold migration
3. CandidateへKV handoff

予測器を実装する前に、oracle最適化の上限を測る。

### Phase 3: Local vs second-nearest予測

現在の2候補だけで`T_local`と`T_handoff`を比較する。容量判定のみの現行Cと比較し、無用なredirect 7件をどこまで削減できるか確認する。

### Phase 4: Multi-candidate routing

近傍上位K GPUへ候補を広げる。GPU 5集中と77秒burstがどこまで減るか評価する。

### Phase 5: Reservationとexternality

転送中reservation、集中ペナルティ、後続requestへの影響を追加する。

## 17. 評価指標

平均TTFTだけでなく次を記録する。

- p50/p95/p99/最大TTFT
- TTFT > 800 ms、1 s、2 sのSLO違反率
- 無用なredirect件数
- Oracle routeとの不一致率
- Candidate別予測誤差
- Router/Scheduler/Network/Prefill breakdown
- Redirect先HHI
- 同一targetへの転送中request数
- GPU間request数・KV使用量・Scheduler queueのCV
- 後続requestを含む総TTFT変化

## 18. 次のsimulation

適応型routingの検証前後で、次の軸をsweepする。

| 軸 | 推奨値 |
|---|---|
| Input tokens | 512、2,000、6,000、12,000 |
| Prefix reuse | 0%、25%、50%、75% |
| Arrival window | 60、90、120秒 |
| Network bandwidth | 5、10.7、25、50、100 Gbit/s |
| Candidate GPUs | 1、2、3、全GPU |
| Network contention | 無効、有効 |

特に90秒版は、クラスタ全体には余力があるが局所hotspotが残るため、routing判断の良否を観察しやすい。

## 19. まとめ

現行routingの長所は、最大context KVを予約して将来のmemory overflowを防ぎ、局所hotspotを簡単な規則で回避できることである。

主な課題:

1. KV容量と実行待ち時間を区別していない
2. Localで短時間待つ選択肢を予測比較していない
3. Redirect先が第2近傍に固定されている
4. 転送中requestの将来負荷を十分に予約していない
5. Request自身とクラスタ全体の最適化を区別していない
6. Router waitが直接計測されていない

次の基本設計は、容量ベースroutingを安全条件として残し、その上に予測TTFT比較を追加することである。

```text
容量判定: そのGPUへ安全に置けるか
TTFT予測: そのGPUで速く処理できるか
Reservation: 他requestと同じ空きを二重利用しないか
Externality: 後続requestを悪化させないか
```

最終的には、固定的なA/B/Cではなく、requestごとに`wait local`、`cold migrate`、`KV handoff`、`target GPU`を同時選択する適応型routerを目指す。
