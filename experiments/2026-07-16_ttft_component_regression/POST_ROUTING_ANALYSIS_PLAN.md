# Post-routing TTFT component支配要因分析計画

## 1. 目的

本解析の目的は、requestのrouting decisionが確定した後の情報を使用し、E2E TTFTを構成する各componentに対して何が支配要因となるかを特定することである。

対象component：

```text
Router queue
Scheduler queue
KV transfer
Compute / prefill
Other communication
E2E TTFT
```

予測精度の最大化だけを目的とせず、各特徴量groupを除いたときに説明力がどれだけ低下するかを測り、componentごとの支配要因を比較する。

## 2. Post-routingとして扱う時点

次の情報が確定した直後を予測時点とする。

- Routing policy
- Redirectされたか
- Target GPU
- KVを移動するか
- Migration tokens/bytes/distance
- Prefix reuseが維持・消失したか
- Routerでのcapacity待ち時間
- Routing decision時点のGPU capacity snapshot

Schedulerによる初回batch投入後にしか確定しない情報は、別の`post-schedule診断モデル`として扱う。

### 2.1 Post-routingモデル

Routing decision直後に利用できる情報を使う。

```text
Request/workload条件
+ routing result
+ realized reuse/KV movement
+ router capacity state
```

### 2.2 Post-schedule診断モデル

初回schedule時のscheduler snapshotも加える。

```text
Post-routing特徴量
+ waiting/running/decode数
+ token budget
+ scheduled prefill/decode tokens
+ prefill work ahead
```

この2モデルを比較し、scheduler内部状態が追加されることで各componentの説明力がどれだけ増えるか確認する。

## 3. Target定義

単位はすべてmsとする。

```text
router_queue_ms
  = max(
      0,
      e2e_ttft_ns
      - queueing_before_ttft_ns
      - prefill_service_ns
      - communication_latency_ns
    ) / 1e6

scheduler_queue_ms
  = queueing_before_ttft_ns / 1e6

kv_transfer_ms
  = kv_migration_latency_ns / 1e6

compute_prefill_ms
  = prefill_service_ns / 1e6

other_communication_ms
  = max(
      0,
      communication_latency_ns - kv_migration_latency_ns
    ) / 1e6

e2e_ttft_ms
  = e2e_ttft_ns / 1e6
```

次の再構成が成立することをdataset生成時に検証する。

```text
router_queue_ms
+ scheduler_queue_ms
+ kv_transfer_ms
+ compute_prefill_ms
+ other_communication_ms
= e2e_ttft_ms
```

## 4. 特徴量group

個別列は強く相関するため、支配要因の順位は列単位ではなくgroup単位で評価する。

### 4.1 Request demand

- `input_tokens`
- `output_tokens_actual`
- `request_payload_bytes`
- `minimum_prefill_chunks`

### 4.2 Nominal reuse

- `nominal_reuse_tokens`
- `nominal_reuse_ratio`
- `nominal_uncached_tokens`

### 4.3 Realized reuse

- `reuse_tokens_realized`
- `reuse_effective`
- `reuse_preservation_ratio`
- `reuse_lost`
- `realized_uncached_tokens`

### 4.4 Offered load

- `request_rate_rps`
- `offered_input_tokens_per_s`
- `offered_uncached_tokens_per_s`
- `global_arrivals_100ms/500ms/1s`
- `global_uncached_tokens_100ms/500ms/1s`

### 4.5 Home-cell load

- `home_arrivals_100ms/500ms/1s`
- `home_uncached_tokens_100ms/500ms/1s`
- `home_workload_share`

### 4.6 Arrival phase

- `arrival_offset_s`
- `interarrival_ms`
- Workload進行率

### 4.7 Policy

- A/B/Cのone-hot表現
- `kv_handoff_enabled`
- `cold_on_redirect`

### 4.8 Routing result

- `rerouted`
- `target_gpu_id`
- Home GPUとtarget GPUが同一か
- Migration先までの距離

GPU ID自体は特定GPUへの依存を学習するため、主要モデルでは除外した版も作る。支配要因の主評価には`rerouted`や負荷量を優先する。

### 4.9 KV movement

- `kv_moved`
- `migration_tokens_realized`
- `migration_bytes_realized`
- `migration_distance_m`
- `migration_bandwidth_gbps`

### 4.10 Router capacity state

- `capacity_running_reqs`
- `capacity_required_kv_bytes`
- `capacity_available_kv_bytes`
- `capacity_projected_active_kv_bytes`
- `capacity_kv_budget_bytes`
- `router_candidate_gpu_count`
- `redirect_capacity_reason`
- `router_capacity_wait_ns`

派生特徴量：

```text
capacity_pressure
  = capacity_required_kv_bytes / max(capacity_available_kv_bytes, 1)

projected_capacity_utilization
  = capacity_projected_active_kv_bytes / capacity_kv_budget_bytes

capacity_deficit_bytes
  = max(0, capacity_required_kv_bytes - capacity_available_kv_bytes)
```

### 4.11 Scheduler state

- `scheduler_waiting_reqs_at_first_schedule`
- `scheduler_running_reqs_at_first_schedule`
- `scheduler_running_decode_reqs_at_first_schedule`
- `scheduler_token_budget_at_first_schedule`
- `scheduler_scheduled_prefill_tokens`
- `scheduler_scheduled_decode_tokens`
- `scheduler_batch_num_seqs`
- `scheduler_prefill_tokens_ahead`

派生特徴量：

```text
scheduler_budget_utilization
  = (scheduled_prefill_tokens + scheduled_decode_tokens)
    / scheduler_token_budget

scheduler_work_ahead
  = scheduler_prefill_tokens_ahead
    + scheduler_running_decode_reqs_at_first_schedule
```

## 5. Component別モデル

### 5.1 Router queue

Router queueは0が多く、正値部分が長いtailを持つため二段階モデルにする。

#### 発生モデル

```text
P(router_queue_ms > 0)
  ← request demand
   + realized reuse
   + offered load
   + home-cell load
   + policy
   + routing result
   + router capacity state
```

Logistic回帰を使用する。

#### 正値量モデル

```text
log1p(router_queue_ms)
  ← 同じ特徴量
```

正値requestだけを対象にRidgeまたはHuber回帰を使用する。

主な問い：

- Capacity pressureがqueue発生を支配するか
- Redirectされたrequestほどrouter queueが長いか
- Request rateよりcell単位のtoken流入量が重要か
- Policy差はcapacity stateを入れた後にも残るか

### 5.2 Scheduler queue

```text
log1p(scheduler_queue_ms)
  ← request demand
   + realized reuse
   + routing result
   + offered load
   + scheduler state
```

Post-routingモデルとpost-schedule診断モデルを比較する。

主な問い：

- `scheduler_prefill_tokens_ahead`が最大の支配要因か
- Running decode数がprefillの初回scheduleを遅らせるか
- Batch token budgetの利用率がqueueを説明するか
- Input sizeの効果はscheduler stateを入れると消えるか

### 5.3 Compute / prefill

```text
compute_prefill_ms
  ← realized_uncached_tokens
   + minimum_prefill_chunks
   + scheduler_scheduled_decode_tokens
   + scheduler_batch_num_seqs
   + realized reuse
```

通常のRidgeを主要baselineとする。必要に応じてtoken区間ごとのpiecewise linear特徴量を加える。

主な問い：

- Realized uncached tokensがcomputeをどこまで説明するか
- 同じuncached tokensでもbatch内decode数でcomputeが変わるか
- KV handoffのcompute便益はrealized reuseで説明できるか

### 5.4 KV transfer

KV transfer時間そのものは回帰による重要度評価を主目的にしない。

```text
kv_transfer_ms
  = distance latency
  + serialization latency
  + CPU staging latency
```

解析する内容：

- `kv_moved=1`となる条件
- Migration tokens/bytesの分布
- KV transferがE2E TTFTに占める比率
- Compute削減量とKV transfer costの差

検算として、解析式による再構成誤差を報告する。

### 5.5 Other communication

Payload、distance、bandwidthから解析的に説明できるか確認する。固定値に近い場合は回帰重要度の対象外とする。

### 5.6 E2E TTFT

二つのモデルを比較する。

#### 直接モデル

```text
log1p(e2e_ttft_ms)
  ← 全post-routing特徴量
```

#### Component合成モデル

```text
predicted_e2e
  = predicted_router_queue
  + predicted_scheduler_queue
  + analytical_kv_transfer
  + predicted_compute
  + analytical_other_communication
```

支配要因の説明にはcomponent合成モデルを優先する。

## 6. 線形basisと相互作用

線形モデルの解釈可能性を保ちつつ、capacity境界を表現する。

### 6.1 Hinge特徴量

```text
capacity_overload
  = max(0, capacity_pressure - 1)

token_load_over_threshold
  = max(0, offered_uncached_tokens_per_s - threshold)
```

### 6.2 主要な相互作用

```text
realized_uncached_tokens × request_rate
reuse_preservation_ratio × policy
rerouted × policy
capacity_pressure × request_rate
scheduler_prefill_tokens_ahead × running_decode_reqs
```

相互作用は事前に定義したものだけを使用し、探索的に大量生成しない。

## 7. 特徴量重要度

### 7.1 標準化係数

数値特徴量を平均0、標準偏差1へ変換し、係数の符号と大きさを報告する。

個別係数は共線性の影響を受けるため、支配要因の主順位には使わない。

### 7.2 Group ablation importance

主要指標とする。

```text
importance(group)
  = held-out MAE(without group)
  - held-out MAE(full model)
```

値が大きいほど、そのgroupがtargetの説明に必要である。

### 7.3 Partial R²

```text
partial_R2(group)
  = R²(full model) - R²(without group)
```

### 7.4 Grouped permutation importance

同じ意味を持つ列をまとめてshuffleする。ただしscenario内で一定のinput size、rate、reuse率はshuffleできないため、これらの評価にはgroup ablationを使用する。

### 7.5 Bootstrap信頼区間

Seedまたはbase trace単位でbootstrapし、各importanceの95%信頼区間を求める。

```text
支配要因候補
  = mean importance > 0
  AND 95% CI lower bound > 0
```

## 8. 共線性対策

以下を実行する。

- Pearson/Spearman相関行列
- Variance Inflation Factor
- 同一情報を持つ派生列の整理
- RidgeとOLSの係数安定性比較
- Feature group単位の評価

主要モデルでは、例えば次を同時に入れすぎない。

```text
input_tokens
request_payload_bytes
minimum_prefill_chunks
```

```text
migration_tokens
migration_bytes
reuse_tokens_realized
```

代表特徴量を使用するモデルと、全特徴量Ridgeの両方を作る。

## 9. Data split

Request行のrandom splitは禁止する。

優先順位：

1. Leave-one-seed-out
2. Leave-one-condition-out
3. Leave-one-input-size-out
4. Leave-one-request-rate-out
5. Leave-one-reuse-level-out

同じtraceを共有するA/B/Cは必ず同じfoldへ入れる。

Hyperparameter選択はtrain fold内で行い、test foldを使用しない。

## 10. Subset別解析

次のsubsetでimportanceを再計算する。

- All requests
- Redirected only
- Non-redirected only
- KV moved only
- Reuse preserved
- Reuse lost
- Policy A/B/C
- Input size別
- Reuse率別
- Request rate別
- Capacity regime別

Capacity regime：

```text
Low pressure:
  capacity_pressure < 0.8

Boundary:
  0.8 <= capacity_pressure <= 1.2

Overloaded:
  capacity_pressure > 1.2
```

## 11. 出力物

### CSV

```text
analysis/post_routing/canonical_requests.csv
analysis/post_routing/feature_inventory.csv
analysis/post_routing/correlation_matrix.csv
analysis/post_routing/vif.csv
analysis/post_routing/model_metrics.csv
analysis/post_routing/standardized_coefficients.csv
analysis/post_routing/group_ablation_importance.csv
analysis/post_routing/partial_r2.csv
analysis/post_routing/bootstrap_importance.csv
analysis/post_routing/subset_importance.csv
analysis/post_routing/kv_cost_benefit.csv
```

### Figures

```text
figures/post_routing/component_share_by_condition.png
figures/post_routing/component_importance_heatmap.png
figures/post_routing/router_queue_occurrence_coefficients.png
figures/post_routing/router_queue_positive_importance.png
figures/post_routing/scheduler_queue_importance.png
figures/post_routing/compute_importance.png
figures/post_routing/importance_by_input_rate_reuse.png
figures/post_routing/kv_cost_vs_compute_saving.png
```

### Report

```text
reports/post_routing_component_dominance.md
```

## 12. 最終結果表

各componentについて次の形式でまとめる。

| Component | Rank | Feature group | Ablation MAE increase | Partial R² | 95% CI | Interpretation |
|---|---:|---|---:|---:|---|---|
| Router queue occurrence | 1 | Router capacity | TBD | TBD | TBD | Capacity境界 |
| Router queue positive | 1 | Offered load | TBD | TBD | TBD | 待ち時間の蓄積 |
| Scheduler queue | 1 | Scheduler state | TBD | TBD | TBD | 先行work量 |
| Compute/prefill | 1 | Realized reuse | TBD | TBD | TBD | Uncached token削減 |
| E2E TTFT | 1 | TBD | TBD | TBD | TBD | Component経由で解釈 |

## 13. 実施順序

1. 完了済みresultのinventoryを作る
2. 新しい状態列を含むcanonical datasetを再構築する
3. Component shareを集計する
4. 相関行列・VIFで共線性を確認する
5. Post-routing Ridge baselineを作る
6. Router queue二段階モデルを作る
7. Scheduler queueへscheduler stateを追加する
8. Group ablationとpartial R²を計算する
9. Seed bootstrap信頼区間を計算する
10. Input/rate/reuse/capacity regime別に再計算する
11. Componentごとの支配要因をreportへまとめる

## 14. 解釈上の注意

Post-routingモデルは、routing結果を知った後にTTFTを説明する診断モデルである。Routing前の予測にはそのまま使用できない。

また、`rerouted`、`kv_moved`、`realized reuse`はpolicyによって決まる媒介変数である。これらを入れた後のpolicy係数はpolicyの総効果ではなく、観測したmechanismで説明されなかった残差差を表す。

したがって本解析で「支配要因」と呼ぶものは、held-out dataの説明に必要な特徴量groupであり、無条件の因果効果を意味しない。
