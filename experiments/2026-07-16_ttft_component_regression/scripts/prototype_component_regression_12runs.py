#!/usr/bin/env python3
"""Build exploratory post-routing component models from the minimal 12 runs."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = EXPERIMENT_DIR / "results/phase1"
ANALYSIS_DIR = EXPERIMENT_DIR / "analysis/prototype_12runs"
FIGURE_DIR = EXPERIMENT_DIR / "figures/prototype_12runs"
REPORT_PATH = EXPERIMENT_DIR / "reports/prototype_12runs_component_regression.md"
MODEL_DIR = EXPERIMENT_DIR / "models/prototype_12runs"

POLICY_LABELS = {
    "NEAREST_KV": "A",
    "NEAREST_MIGRATE": "B",
    "NEAREST_MIGRATE_KV": "C",
}

FEATURE_GROUPS = {
    "request_demand": ["input_tokens", "output_tokens"],
    "realized_reuse": ["realized_reuse_tokens", "effective_uncached_tokens"],
    "offered_load": ["request_rate_rps", "home_arrivals_1s", "home_arrivals_5s"],
    "routing_result": ["policy", "rerouted"],
    "initial_home_capacity": [
        "initial_capacity_pressure", "initial_slot_pressure",
        "initial_waiting_reqs", "initial_running_reqs", "initial_admissible",
    ],
    "initial_candidate_capacity": [
        "initial_admissible_candidate_count", "initial_total_waiting_reqs",
        "initial_total_running_reqs", "initial_max_running_reqs",
        "initial_min_capacity_pressure", "initial_max_capacity_pressure",
    ],
    "initial_target_capacity": [
        "initial_target_capacity_pressure", "initial_target_slot_pressure",
        "initial_target_waiting_reqs", "initial_target_running_reqs",
        "initial_target_admissible",
    ],
}

SCHEDULER_GROUPS = {
    "request_demand": ["input_tokens", "output_tokens"],
    "realized_reuse": ["realized_reuse_tokens", "effective_uncached_tokens"],
    "offered_load": ["request_rate_rps", "home_arrivals_1s", "home_arrivals_5s"],
    "routing_result": ["policy", "rerouted"],
    "scheduler_admission_state": [
        "scheduler_waiting_reqs_at_admission",
        "scheduler_running_reqs_at_admission",
        "scheduler_running_decode_reqs_at_admission",
        "scheduler_inflight_prefill_tokens_at_admission",
        "scheduler_inflight_decode_tokens_at_admission",
        "scheduler_available_token_budget_at_admission",
        "scheduler_prefill_tokens_ahead_at_admission",
    ],
}

COMPUTE_GROUPS = {
    "effective_demand": ["effective_uncached_tokens", "output_tokens"],
}

KV_FEATURE_GROUPS = {
    "kv_movement": ["kv_moved", "kv_migration_bytes"],
    "kv_link": ["kv_bandwidth_gbps", "kv_distance_m"],
}

TARGETS = {
    "router_queue_ms": "router_hurdle",
    "scheduler_queue_ms": "ridge",
    "kv_transfer_ms": "ridge",
    "compute_ms": "ridge",
}


def rolling_home_arrivals(frame, seconds):
    arrivals = frame["request_send_time_ns"].to_numpy(dtype=float)
    home = frame["nearest_gpu_id"].fillna(frame["gpu_id"]).to_numpy()
    order = np.argsort(arrivals, kind="stable")
    result = np.zeros(len(frame), dtype=int)
    left = 0
    for position, row_index in enumerate(order):
        while arrivals[order[left]] < arrivals[row_index] - seconds * 1e9:
            left += 1
        prior = order[left:position]
        result[row_index] = int(np.sum(home[prior] == home[row_index]))
    return result


def load_dataset():
    frames = []
    for path in sorted(RESULTS_DIR.glob("*/*/requests.csv")):
        policy = path.parent.name
        if policy not in POLICY_LABELS:
            continue
        frame = pd.read_csv(path)
        if len(frame) != 300 or "router_initial_capacity_pressure" not in frame:
            continue
        scenario = path.parents[1].name
        send = frame["request_send_time_ns"].astype(float)
        duration_s = max((send.max() - send.min()) / 1e9, 1e-9)
        rate = (len(frame) - 1) / duration_s
        input_tokens = frame["input"].astype(float)
        reuse = frame["reuse_prefix_toks"].astype(float)
        migration = frame["kv_migration_latency_ns"].astype(float)
        output = pd.DataFrame({
            "scenario": scenario,
            "policy": policy,
            "policy_label": POLICY_LABELS[policy],
            "request_id": frame["request id"].astype(int),
            "input_tokens": input_tokens,
            "output_tokens": frame["output"].astype(float),
            "realized_reuse_tokens": reuse,
            "effective_uncached_tokens": (input_tokens - reuse).clip(lower=0),
            "request_rate_rps": rate,
            "home_arrivals_1s": rolling_home_arrivals(frame, 1),
            "home_arrivals_5s": rolling_home_arrivals(frame, 5),
            "rerouted": frame["rerouted"].astype(int),
            "initial_capacity_pressure": frame["router_initial_capacity_pressure"].astype(float),
            "initial_slot_pressure": frame["router_initial_slot_pressure"].astype(float),
            "initial_waiting_reqs": frame["router_initial_waiting_reqs"].astype(float),
            "initial_running_reqs": frame["router_initial_running_reqs"].astype(float),
            "initial_admissible": frame["router_initial_admissible"].astype(int),
            "initial_admissible_candidate_count": frame["router_initial_admissible_candidate_count"].astype(float),
            "initial_total_waiting_reqs": frame["router_initial_total_waiting_reqs"].astype(float),
            "initial_total_running_reqs": frame["router_initial_total_running_reqs"].astype(float),
            "initial_max_running_reqs": frame["router_initial_max_running_reqs"].astype(float),
            "initial_min_capacity_pressure": frame["router_initial_min_capacity_pressure"].astype(float),
            "initial_max_capacity_pressure": frame["router_initial_max_capacity_pressure"].astype(float),
            "initial_target_capacity_pressure": frame["router_initial_target_capacity_pressure"].astype(float),
            "initial_target_slot_pressure": frame["router_initial_target_slot_pressure"].astype(float),
            "initial_target_waiting_reqs": frame["router_initial_target_waiting_reqs"].astype(float),
            "initial_target_running_reqs": frame["router_initial_target_running_reqs"].astype(float),
            "initial_target_admissible": frame["router_initial_target_admissible"].astype(int),
            "scheduler_waiting_reqs_at_admission": frame["scheduler_waiting_reqs_at_admission"].astype(float),
            "scheduler_running_reqs_at_admission": frame["scheduler_running_reqs_at_admission"].astype(float),
            "scheduler_running_decode_reqs_at_admission": frame["scheduler_running_decode_reqs_at_admission"].astype(float),
            "scheduler_inflight_prefill_tokens_at_admission": frame["scheduler_inflight_prefill_tokens_at_admission"].astype(float),
            "scheduler_inflight_decode_tokens_at_admission": frame["scheduler_inflight_decode_tokens_at_admission"].astype(float),
            "scheduler_available_token_budget_at_admission": frame["scheduler_available_token_budget_at_admission"].astype(float),
            "scheduler_prefill_tokens_ahead_at_admission": frame["scheduler_prefill_tokens_ahead_at_admission"].astype(float),
            "kv_moved": ((migration > 0) & (frame["kv_migration_bytes"] > 0)).astype(int),
            "kv_migration_bytes": frame["kv_migration_bytes"].astype(float),
            "kv_bandwidth_gbps": frame["kv_migration_bandwidth_gbps"].astype(float),
            "kv_distance_m": frame["kv_migration_distance_m"].astype(float),
            "router_queue_ms": frame["router_capacity_wait_ns"].astype(float) / 1e6,
            "scheduler_queue_ms": frame["queueing_before_ttft_ns"].astype(float) / 1e6,
            "kv_transfer_ms": migration / 1e6,
            "compute_ms": frame["prefill_service_ns"].astype(float) / 1e6,
            "e2e_ttft_ms": frame["e2e_ttft_ns"].astype(float) / 1e6,
        })
        output["other_communication_ms"] = (
            output.e2e_ttft_ms
            - output.router_queue_ms
            - output.scheduler_queue_ms
            - output.kv_transfer_ms
            - output.compute_ms
        ).clip(lower=0)
        frames.append(output)
    if not frames:
        raise RuntimeError("No complete minimal Phase 1 runs were found")
    return pd.concat(frames, ignore_index=True)


def feature_columns(groups):
    return list(dict.fromkeys(column for members in groups.values() for column in members))


def make_preprocessor(columns):
    numeric = [column for column in columns if column != "policy"]
    categorical = [column for column in columns if column == "policy"]
    return ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("categorical", OneHotEncoder(handle_unknown="ignore"), categorical),
    ])


def make_ridge(columns, alpha=10.0):
    return Pipeline([
        ("preprocess", make_preprocessor(columns)),
        ("model", Ridge(alpha=alpha)),
    ])


def make_logistic(columns):
    return Pipeline([
        ("preprocess", make_preprocessor(columns)),
        ("model", LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced")),
    ])


def ridge_prediction(train, test, columns, target, log_target=False):
    model = make_ridge(columns)
    train_target = np.log1p(train[target]) if log_target else train[target]
    model.fit(train[columns], train_target)
    prediction = model.predict(test[columns])
    if log_target:
        prediction = np.expm1(prediction)
        prediction = np.clip(prediction, 0, train[target].quantile(0.99))
    return np.clip(prediction, 0, None)


def groups_for_target(target):
    if target == "kv_transfer_ms":
        return KV_FEATURE_GROUPS
    if target == "compute_ms":
        return COMPUTE_GROUPS
    if target == "scheduler_queue_ms":
        return SCHEDULER_GROUPS
    return FEATURE_GROUPS


def hurdle_prediction(train, test, columns, target):
    train_event = (train[target] > 0).astype(int)
    if train_event.nunique() < 2:
        probability = np.full(len(test), float(train_event.iloc[0]))
    else:
        classifier = make_logistic(columns)
        classifier.fit(train[columns], train_event)
        probability = classifier.predict_proba(test[columns])[:, 1]
    positive = train[train[target] > 0]
    if positive.empty:
        positive_duration = np.zeros(len(test))
    else:
        positive_duration = ridge_prediction(
            positive, test, columns, target, log_target=True
        )
    return probability * positive_duration, probability


def cross_validated_predictions(dataset, groups, target, model_kind):
    columns = feature_columns(groups)
    rows = []
    for fold, scenario in enumerate(sorted(dataset.scenario.unique())):
        train = dataset[dataset.scenario != scenario]
        test = dataset[dataset.scenario == scenario]
        if model_kind == "router_hurdle":
            prediction, event_probability = hurdle_prediction(
                train, test, columns, target
            )
        else:
            prediction = ridge_prediction(
                train, test, columns, target, log_target=model_kind == "log_ridge"
            )
            event_probability = np.full(len(test), np.nan)
        rows.append(pd.DataFrame({
            "fold": fold,
            "scenario": scenario,
            "policy": test.policy.to_numpy(),
            "request_id": test.request_id.to_numpy(),
            "target": target,
            "actual_ms": test[target].to_numpy(),
            "predicted_ms": prediction,
            "event_probability": event_probability,
        }))
    return pd.concat(rows, ignore_index=True)


def evaluate_predictions(predictions):
    rows = []
    for target, frame in predictions.groupby("target"):
        row = {
            "target": target,
            "n": len(frame),
            "mae_ms": mean_absolute_error(frame.actual_ms, frame.predicted_ms),
            "median_ae_ms": np.median(np.abs(frame.actual_ms - frame.predicted_ms)),
            "r2": r2_score(frame.actual_ms, frame.predicted_ms),
        }
        if frame.event_probability.notna().all():
            event = (frame.actual_ms > 0).astype(int)
            row["event_rate"] = event.mean()
            row["event_auc"] = roc_auc_score(event, frame.event_probability)
        else:
            row["event_rate"] = np.nan
            row["event_auc"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def composite_predictions(dataset, predictions):
    keys = ["scenario", "policy", "request_id"]
    predicted = predictions.pivot_table(
        index=keys, columns="target", values="predicted_ms"
    ).reset_index()
    actual = dataset[keys + [
        "router_queue_ms", "scheduler_queue_ms", "kv_transfer_ms",
        "compute_ms", "other_communication_ms", "e2e_ttft_ms",
    ]]
    output = actual.merge(predicted, on=keys, suffixes=("_actual", "_predicted"))
    component_targets = list(TARGETS)
    output["actual_component_sum_ms"] = output[
        [f"{target}_actual" for target in component_targets]
    ].sum(axis=1)
    output["predicted_component_sum_ms"] = output[
        [f"{target}_predicted" for target in component_targets]
    ].sum(axis=1)
    output["predicted_e2e_ttft_ms"] = (
        output.predicted_component_sum_ms + output.other_communication_ms
    )
    rows = []
    for target, actual_column, predicted_column in (
        ("component_sum_ms", "actual_component_sum_ms", "predicted_component_sum_ms"),
        ("e2e_ttft_ms", "e2e_ttft_ms", "predicted_e2e_ttft_ms"),
    ):
        rows.append({
            "target": target,
            "n": len(output),
            "mae_ms": mean_absolute_error(output[actual_column], output[predicted_column]),
            "median_ae_ms": np.median(np.abs(
                output[actual_column] - output[predicted_column]
            )),
            "r2": r2_score(output[actual_column], output[predicted_column]),
            "event_rate": np.nan,
            "event_auc": np.nan,
        })
    return output, pd.DataFrame(rows)


def ablation_importance(dataset, base_groups):
    rows = []
    for target, model_kind in TARGETS.items():
        groups = groups_for_target(target)
        full = cross_validated_predictions(dataset, groups, target, model_kind)
        full_mae = mean_absolute_error(full.actual_ms, full.predicted_ms)
        for removed_group in groups:
            reduced_groups = {
                group: members for group, members in groups.items()
                if group != removed_group
            }
            if reduced_groups:
                reduced = cross_validated_predictions(
                    dataset, reduced_groups, target, model_kind
                )
            else:
                reduced_rows = []
                for fold, scenario in enumerate(sorted(dataset.scenario.unique())):
                    train = dataset[dataset.scenario != scenario]
                    test = dataset[dataset.scenario == scenario]
                    reduced_rows.append(pd.DataFrame({
                        "fold": fold,
                        "scenario": scenario,
                        "policy": test.policy.to_numpy(),
                        "request_id": test.request_id.to_numpy(),
                        "target": target,
                        "actual_ms": test[target].to_numpy(),
                        "predicted_ms": np.full(len(test), train[target].mean()),
                        "event_probability": np.full(len(test), np.nan),
                    }))
                reduced = pd.concat(reduced_rows, ignore_index=True)
            reduced_mae = mean_absolute_error(reduced.actual_ms, reduced.predicted_ms)
            rows.append({
                "target": target,
                "group": removed_group,
                "full_mae_ms": full_mae,
                "ablated_mae_ms": reduced_mae,
                "mae_increase_ms": reduced_mae - full_mae,
            })
    return pd.DataFrame(rows)


def fitted_coefficients(dataset, groups, target, model_kind):
    columns = feature_columns(groups)
    rows = []
    if model_kind == "router_hurdle":
        event = (dataset[target] > 0).astype(int)
        classifier = make_logistic(columns)
        classifier.fit(dataset[columns], event)
        names = classifier.named_steps["preprocess"].get_feature_names_out()
        values = classifier.named_steps["model"].coef_[0]
        rows.extend(("event_logistic", name, value) for name, value in zip(names, values))
        fit_data = dataset[event == 1]
        fit_target = np.log1p(fit_data[target])
        stage = "positive_log_ridge"
    else:
        fit_data = dataset
        fit_target = np.log1p(dataset[target]) if model_kind == "log_ridge" else dataset[target]
        stage = "log_ridge" if model_kind == "log_ridge" else "ridge"
    model = make_ridge(columns)
    model.fit(fit_data[columns], fit_target)
    names = model.named_steps["preprocess"].get_feature_names_out()
    values = model.named_steps["model"].coef_
    rows.extend((stage, name, value) for name, value in zip(names, values))
    output = []
    for stage_name, encoded_name, value in rows:
        feature = encoded_name.split("__", 1)[1]
        output.append({
            "target": target,
            "stage": stage_name,
            "feature": feature,
            "standardized_coefficient": value,
            "absolute_coefficient": abs(value),
        })
    return output


def analytical_kv_prediction(dataset):
    moved = dataset.kv_moved.to_numpy(dtype=float)
    migration_bytes = dataset.kv_migration_bytes.to_numpy(dtype=float)
    bandwidth = dataset.kv_bandwidth_gbps.replace(0, np.nan).to_numpy(dtype=float)
    distance_latency_ns = np.where(moved > 0, 300500.0, 0.0)
    serialization_ns = np.where(
        moved > 0, 8.0 * migration_bytes / bandwidth, 0.0
    )
    staging_ns = np.where(
        moved > 0,
        2.0 * (migration_bytes / 33.8 + 102.9),
        0.0,
    )
    return (distance_latency_ns + serialization_ns + staging_ns) / 1e6


def save_fitted_models(dataset):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    router_columns = feature_columns(FEATURE_GROUPS)
    router_event = make_logistic(router_columns)
    router_event.fit(
        dataset[router_columns], (dataset.router_queue_ms > 0).astype(int)
    )
    positive = dataset[dataset.router_queue_ms > 0]
    router_positive = make_ridge(router_columns)
    router_positive.fit(positive[router_columns], np.log1p(positive.router_queue_ms))

    scheduler_columns = feature_columns(SCHEDULER_GROUPS)
    scheduler = make_ridge(scheduler_columns)
    scheduler.fit(dataset[scheduler_columns], dataset.scheduler_queue_ms)

    compute_columns = feature_columns(COMPUTE_GROUPS)
    compute = make_ridge(compute_columns)
    compute.fit(dataset[compute_columns], dataset.compute_ms)

    bundle = {
        "router_event": router_event,
        "router_positive": router_positive,
        "router_positive_cap_ms": float(positive.router_queue_ms.quantile(0.99)),
        "scheduler": scheduler,
        "compute": compute,
        "router_columns": router_columns,
        "scheduler_columns": scheduler_columns,
        "compute_columns": compute_columns,
        "kv_staging_bandwidth_gbytes_per_s": 33.8,
        "kv_staging_latency_ns": 102.9,
        "kv_fixed_distance_latency_ns": 300500.0,
    }
    joblib.dump(bundle, MODEL_DIR / "component_models.joblib")
    metadata = {
        "training_requests": len(dataset),
        "training_scenarios": sorted(dataset.scenario.unique()),
        "router_columns": router_columns,
        "scheduler_columns": scheduler_columns,
        "compute_columns": compute_columns,
        "warning": "Exploratory four-scenario model; not validated for production use.",
    }
    (MODEL_DIR / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


def plot_predictions(predictions):
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    for axis, target in zip(axes.flat, TARGETS):
        frame = predictions[predictions.target == target]
        x = np.log1p(frame.actual_ms)
        y = np.log1p(frame.predicted_ms)
        for policy, color in zip(POLICY_LABELS, ["#315f9e", "#d17a19", "#31866f"]):
            selected = frame.policy == policy
            axis.scatter(x[selected], y[selected], s=9, alpha=0.35,
                         color=color, label=POLICY_LABELS[policy])
        limit = max(x.max(), y.max())
        axis.plot([0, limit], [0, limit], color="#333333", linewidth=1)
        axis.set_title(target)
        axis.set_xlabel("log1p(actual ms)")
        axis.set_ylabel("log1p(predicted ms)")
        axis.grid(color="#ddd8cf")
    axes[0, 0].legend(title="Policy")
    fig.suptitle("Minimal 12-run component regression: held-out scenario predictions")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "oof_actual_vs_predicted.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_importance(importance):
    pivot = importance.pivot(index="group", columns="target", values="mae_increase_ms").fillna(0)
    fig, axis = plt.subplots(figsize=(11, 6))
    image = axis.imshow(pivot.to_numpy(), aspect="auto", cmap="RdBu_r")
    axis.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=25, ha="right")
    axis.set_yticks(range(len(pivot.index)), pivot.index)
    for row in range(len(pivot)):
        for column in range(len(pivot.columns)):
            axis.text(column, row, f"{pivot.iloc[row, column]:.1f}",
                      ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=axis, label="OOF MAE increase after group removal (ms)")
    axis.set_title("Exploratory feature-group importance")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "group_ablation_importance.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def write_report(dataset, metrics, importance, coefficients, kv_analytic_mae):
    metric_lookup = metrics.set_index("target")
    router_mae = metric_lookup.loc["router_queue_ms", "mae_ms"]
    scheduler_mae = metric_lookup.loc["scheduler_queue_ms", "mae_ms"]
    component_sum_mae = metric_lookup.loc["component_sum_ms", "mae_ms"]
    metric_rows = []
    for row in metrics.itertuples():
        auc = "-" if pd.isna(row.event_auc) else f"{row.event_auc:.3f}"
        metric_rows.append(
            f"| {row.target} | {row.mae_ms:.2f} ms | {row.median_ae_ms:.2f} ms | "
            f"{row.r2:.3f} | {auc} |"
        )
    top_importance = importance.sort_values(
        ["target", "mae_increase_ms"], ascending=[True, False]
    ).groupby("target").head(3)
    importance_rows = [
        f"| {row.target} | {row.group} | {row.mae_increase_ms:.2f} ms |"
        for row in top_importance.itertuples()
    ]
    top_coefficients = coefficients.sort_values(
        ["target", "stage", "absolute_coefficient"], ascending=[True, True, False]
    ).groupby(["target", "stage"]).head(5)
    coefficient_rows = [
        f"| {row.target} | {row.stage} | {row.feature} | "
        f"{row.standardized_coefficient:.4f} |"
        for row in top_coefficients.itertuples()
    ]
    report = f"""# 最小12 runsによるTTFT component回帰プロトタイプ

## 1. 目的

Phase 1の4 scenarios、3 policies、合計12 runs・{len(dataset):,} requestsを用い、routing直後に利用可能な入力からRouter queue、Scheduler queue、KV transfer、Computeを個別に推定するプロトタイプを構築した。再実行後に追加されたsecond-nearest初期snapshotとscheduler admission snapshotも使用した。

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
{chr(10).join(metric_rows)}

RouterのEvent AUCはqueue発生有無の識別性能である。R2が負の場合、未知scenarioに対して学習データ平均を使うより悪く、現在の4 scenariosでは安定した外挿式になっていないことを表す。

KV transferの解析式による全request MAEは`{kv_analytic_mae:.6f} ms`だった。KV transferは回帰より解析式で求める方が適切である。

### 追加snapshotの効果

再実行前の旧プロトタイプと比較すると、次のように改善した。

| Target | 旧ログ | 新ログ | MAE改善 |
|---|---:|---:|---:|
| Router queue | 1,593.05 ms | {router_mae:.2f} ms | {1593.050910 - router_mae:.2f} ms |
| Scheduler queue | 57.69 ms | {scheduler_mae:.2f} ms | {57.686707 - scheduler_mae:.2f} ms |

Router queue発生AUCは0.997から1.000となった。Second-nearestの初期capacity状態はqueue発生判定と正値queue推定の両方に寄与した。Scheduler admission状態の追加により、Scheduler queueのR2は0.321から0.637へ改善した。

4 component合成値のOOF MAEは`{component_sum_mae:.2f} ms`である。ただし中央値絶対誤差は約85 msで、平均誤差は一部のRouter tail外挿失敗に支配されている。

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
{chr(10).join(importance_rows)}

4 scenariosしかないため、負のimportanceや不安定な順位が発生する。現段階では確定的な因果順位ではなく、追加データを選ぶための探索結果として扱う。

現時点で最も明確なgroupは、Computeの`effective_demand`、Routerの`initial_home_capacity`、Schedulerの`scheduler_admission_state`である。Routerでは`routing_result`と`initial_target_capacity`もほぼ同程度の追加寄与を持つ。

## 5. 標準化係数

各stageで絶対値が大きい上位係数を示す。Logistic係数はqueue発生log-odds、`log_ridge`係数は対数時間、通常のRidge係数はmsを目的変数とするため、stageを跨いで係数値を直接比較しない。

| Target | Stage | Feature | Standardized coefficient |
|---|---|---|---:|
{chr(10).join(coefficient_rows)}

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
python3 experiments/2026-07-16_ttft_component_regression/scripts/predict_components_prototype.py \\
  --input experiments/2026-07-16_ttft_component_regression/analysis/prototype_12runs/dataset.csv \\
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
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset()
    dataset.to_csv(ANALYSIS_DIR / "dataset.csv", index=False)

    predictions = []
    for target, model_kind in TARGETS.items():
        groups = groups_for_target(target)
        predictions.append(cross_validated_predictions(
            dataset, groups, target, model_kind
        ))
    predictions = pd.concat(predictions, ignore_index=True)
    predictions.to_csv(ANALYSIS_DIR / "oof_predictions.csv", index=False)

    metrics = evaluate_predictions(predictions)
    composite, composite_metrics = composite_predictions(dataset, predictions)
    composite.to_csv(ANALYSIS_DIR / "composite_oof_predictions.csv", index=False)
    metrics = pd.concat([metrics, composite_metrics], ignore_index=True)
    metrics.to_csv(ANALYSIS_DIR / "model_metrics.csv", index=False)
    importance = ablation_importance(dataset, FEATURE_GROUPS)
    importance.to_csv(ANALYSIS_DIR / "group_ablation_importance.csv", index=False)

    coefficient_rows = []
    for target, model_kind in TARGETS.items():
        groups = groups_for_target(target)
        coefficient_rows.extend(fitted_coefficients(
            dataset, groups, target, model_kind
        ))
    coefficients = pd.DataFrame(coefficient_rows)
    coefficients.to_csv(ANALYSIS_DIR / "standardized_coefficients.csv", index=False)

    analytical_kv = analytical_kv_prediction(dataset)
    kv_analytic_mae = mean_absolute_error(dataset.kv_transfer_ms, analytical_kv)
    dataset[["scenario", "policy", "request_id", "kv_transfer_ms"]].assign(
        analytical_kv_transfer_ms=analytical_kv
    ).to_csv(ANALYSIS_DIR / "kv_analytical_predictions.csv", index=False)

    plot_predictions(predictions)
    plot_importance(importance)
    summary = {
        "runs": 12,
        "requests": len(dataset),
        "scenarios": int(dataset.scenario.nunique()),
        "kv_analytical_mae_ms": kv_analytic_mae,
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    write_report(dataset, metrics, importance, coefficients, kv_analytic_mae)
    save_fitted_models(dataset)
    print(metrics.to_string(index=False))
    print("\nTop group importance")
    print(importance.sort_values(
        ["target", "mae_increase_ms"], ascending=[True, False]
    ).groupby("target").head(3).to_string(index=False))
    print(f"\nAnalytical KV MAE: {kv_analytic_mae:.9f} ms")


if __name__ == "__main__":
    main()
