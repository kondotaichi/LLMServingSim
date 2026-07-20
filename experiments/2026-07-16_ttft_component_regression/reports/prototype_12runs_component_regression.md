# 最小12 runsによるTTFT component回帰プロトタイプ

## 1. 目的

Phase 1の4 scenarios、3 policies、合計12 runs・3,600 requestsを用い、routing直後に利用可能な入力からRouter queue、Scheduler queue、KV transfer、Computeを個別に推定するプロトタイプを構築した。再実行後に追加されたsecond-nearest初期snapshotとscheduler admission snapshotも使用した。

同じscenarioのA/B/Cを同じfoldへ入れるleave-one-scenario-out評価を使用した。したがって、評価値は既知requestの補間ではなく、未学習のinput・rate・reuse条件への外挿性能を表す。

## 2. モデル

```text
Predicted E2E TTFT
  = Predicted Router queue
  + Predicted Scheduler queue
  + Predicted KV transfer
  + Predicted Compute
  + Other communication
```

- Router queue：queue発生Logistic回帰と、正値queueの`log1p` Ridgeを掛け合わせるhurdle model
- Scheduler queue：admission時点のwaiting/running、inflight tokens、残budgetを入力するRidge
- KV transfer：Ridgeと既知の転送式を併用
- Compute：Ridge

`router_capacity_retry_count`、`router_decision_*`、`scheduler_*_at_first_schedule`は待ち時間経過後に確定するため、予測特徴量から除外した。

## 3. Leave-one-scenario-out性能

| Target | OOF MAE | Median AE | OOF R2 | Event AUC |
|---|---:|---:|---:|---:|
| compute_ms | 76.03 ms | 65.51 ms | 0.821 | - |
| kv_transfer_ms | 3.41 ms | 0.00 ms | -0.008 | - |
| router_queue_ms | 1326.20 ms | 0.00 ms | -1.582 | 1.000 |
| scheduler_queue_ms | 40.61 ms | 12.84 ms | 0.637 | - |
| component_sum_ms | 1410.98 ms | 84.85 ms | -1.514 | - |
| e2e_ttft_ms | 1411.02 ms | 84.85 ms | -1.514 | - |

RouterのEvent AUCはqueue発生有無の識別性能である。R2が負の場合、未知scenarioに対して学習データ平均を使うより悪く、現在の4 scenariosでは安定した外挿式になっていないことを表す。

KV transferの解析式による全request MAEは`0.000000 ms`だった。KV transferは回帰より解析式で求める方が適切である。

### 追加snapshotの効果

再実行前の旧プロトタイプと比較すると、次のように改善した。

| Target | 旧ログ | 新ログ | MAE改善 |
|---|---:|---:|---:|
| Router queue | 1,593.05 ms | 1326.20 ms | 266.85 ms |
| Scheduler queue | 57.69 ms | 40.61 ms | 17.08 ms |

Router queue発生AUCは0.997から1.000となった。Second-nearestの初期capacity状態はqueue発生判定と正値queue推定の両方に寄与した。Scheduler admission状態の追加により、Scheduler queueのR2は0.321から0.637へ改善した。

4 component合成値のOOF MAEは`1410.98 ms`である。ただし中央値絶対誤差は約85 msで、平均誤差は一部のRouter tail外挿失敗に支配されている。

### Scenario別の注意

- Input 4000：Router queueは0であり、ほぼ完全に判定できた
- Input 8000 / reuse 0%：Router queue R2は約0.48
- Input 8000 / reuse 50%：Router queue R2は約0.53
- Input 6000 / 3.33 req/s：Router queueを平均0.91秒に対して4.60秒と過大予測し、全体R2を悪化させた

Input 6000のrate・reuse対照条件が不足しているため、capacity境界での正値queue長をまだ外挿できていない。

## 4. Feature-group寄与度

値は、そのgroupを除いて再学習したときのOOF MAE増加である。正値が大きいほど予測に重要である。

| Target | Feature group | MAE increase |
|---|---|---:|
| compute_ms | effective_demand | 189.13 ms |
| kv_transfer_ms | kv_movement | 0.01 ms |
| kv_transfer_ms | kv_link | 0.00 ms |
| router_queue_ms | initial_home_capacity | 4390.03 ms |
| router_queue_ms | routing_result | 273.79 ms |
| router_queue_ms | initial_target_capacity | 266.85 ms |
| scheduler_queue_ms | scheduler_admission_state | 21.56 ms |
| scheduler_queue_ms | request_demand | 6.79 ms |
| scheduler_queue_ms | realized_reuse | 2.93 ms |

4 scenariosしかないため、負のimportanceや不安定な順位が発生する。現段階では確定的な因果順位ではなく、追加データを選ぶための探索結果として扱う。

現時点で最も明確なgroupは、Computeの`effective_demand`、Routerの`initial_home_capacity`、Schedulerの`scheduler_admission_state`である。Routerでは`routing_result`と`initial_target_capacity`もほぼ同程度の追加寄与を持つ。

## 5. 標準化係数

各stageで絶対値が大きい上位係数を示す。Logistic係数はqueue発生log-odds、`log_ridge`係数は対数時間、通常のRidge係数はmsを目的変数とするため、stageを跨いで係数値を直接比較しない。

| Target | Stage | Feature | Standardized coefficient |
|---|---|---|---:|
| compute_ms | ridge | effective_uncached_tokens | 221.7942 |
| compute_ms | ridge | output_tokens | -2.3953 |
| kv_transfer_ms | ridge | kv_distance_m | 12.6015 |
| kv_transfer_ms | ridge | kv_bandwidth_gbps | 12.6015 |
| kv_transfer_ms | ridge | kv_moved | 12.6015 |
| kv_transfer_ms | ridge | kv_migration_bytes | 0.0057 |
| router_queue_ms | event_logistic | initial_admissible | -5.2065 |
| router_queue_ms | event_logistic | initial_target_admissible | -2.8472 |
| router_queue_ms | event_logistic | rerouted | -1.7376 |
| router_queue_ms | event_logistic | policy_NEAREST_KV | 1.3576 |
| router_queue_ms | event_logistic | initial_capacity_pressure | 0.8667 |
| router_queue_ms | positive_log_ridge | policy_NEAREST_KV | 0.5889 |
| router_queue_ms | positive_log_ridge | rerouted | 0.4025 |
| router_queue_ms | positive_log_ridge | policy_NEAREST_MIGRATE_KV | -0.3269 |
| router_queue_ms | positive_log_ridge | initial_target_running_reqs | -0.3064 |
| router_queue_ms | positive_log_ridge | initial_target_slot_pressure | -0.3064 |
| scheduler_queue_ms | ridge | scheduler_running_reqs_at_admission | 118.7268 |
| scheduler_queue_ms | ridge | scheduler_prefill_tokens_ahead_at_admission | 67.0814 |
| scheduler_queue_ms | ridge | scheduler_inflight_decode_tokens_at_admission | -59.4225 |
| scheduler_queue_ms | ridge | scheduler_running_decode_reqs_at_admission | -59.4225 |
| scheduler_queue_ms | ridge | scheduler_inflight_prefill_tokens_at_admission | 30.2887 |

Input tokens、reuse tokens、effective uncached tokens、capacity pressureは相互に強く相関する。個別係数の符号を因果効果と解釈せず、group ablationを主指標とする。

## 6. 現時点での定式化

```text
Router queue prediction
  = P(queue > 0 | request, load, routing, initial capacity)
    x E(positive queue | request, load, routing, initial capacity)

Scheduler queue prediction
  = Ridge(request, load, routing, initial capacity)

KV transfer prediction
  = kv_moved
    x (distance latency + serialization latency + two-way staging latency)

Compute prediction
  = Ridge(effective uncached tokens, output tokens)
```

## 7. 解釈上の制約

1. Scenarioは4種類、各seed 1のみであり、係数の信頼区間をまだ求められない
2. KV transfer正値はinput 8000・reuse 50%・policy Cの29 requestsだけである
3. Second-nearest初期状態は記録したが、capacity wait中の将来のKV解放時刻までは入力できない
4. Scheduler admission状態は記録したが、requestが実際に参加する将来batchの構成は確定していない
5. Input、rate、reuseの組合せがfactorialではなく、主効果と相互作用を分離しにくい

## 8. 次の段階

このプロトタイプで新しいsnapshotを含む学習・評価パイプラインは構築できた。正式な定式化には、追加のrate・reuse条件と現在の4条件のseed 2・3を加える。その後、scenario bootstrapによる95%信頼区間を付け、係数とgroup importanceがcondition・seed間で再現するか確認する。

## 9. 学習済みモデルによる計算

学習済みpipelineは`models/prototype_12runs/component_models.joblib`へ保存した。必要特徴量を持つcanonical CSVに対して、次のコマンドでcomponent予測を計算できる。

```bash
python3 experiments/2026-07-16_ttft_component_regression/scripts/predict_components_prototype.py \
  --input experiments/2026-07-16_ttft_component_regression/analysis/prototype_12runs/dataset.csv \
  --output /tmp/prototype_component_predictions.csv
```

出力には次の列が追加される。

- `predicted_router_queue_probability`
- `predicted_router_positive_ms`
- `predicted_router_queue_ms`
- `predicted_scheduler_queue_ms`
- `predicted_kv_transfer_ms`
- `predicted_compute_ms`
- `predicted_component_sum_ms`

`predicted_component_sum_ms`にはaccess RTTなどの`other communication`を含まない。

## 10. 出力

- `analysis/prototype_12runs/dataset.csv`
- `analysis/prototype_12runs/model_metrics.csv`
- `analysis/prototype_12runs/oof_predictions.csv`
- `analysis/prototype_12runs/composite_oof_predictions.csv`
- `analysis/prototype_12runs/group_ablation_importance.csv`
- `analysis/prototype_12runs/standardized_coefficients.csv`
- `analysis/prototype_12runs/summary.json`
- `figures/prototype_12runs/oof_actual_vs_predicted.png`
- `figures/prototype_12runs/group_ablation_importance.png`
- `models/prototype_12runs/component_models.joblib`
- `models/prototype_12runs/metadata.json`
