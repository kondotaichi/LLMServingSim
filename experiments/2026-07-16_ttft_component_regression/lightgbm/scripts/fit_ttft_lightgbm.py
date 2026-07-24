#!/usr/bin/env python3
"""Fit and evaluate a LightGBM TTFT component model.

The evaluation mirrors fit_ttft_formula.py: every scenario is held out once,
communication is omitted from the point prediction, and router wait uses a
two-stage event-probability times positive-duration estimate.
"""

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)


LIGHTGBM_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = EXPERIMENT_ROOT / "analysis/post_routing/canonical_requests.csv"
BASELINE_SUMMARY = EXPERIMENT_ROOT / "analysis/ttft_formula/summary.json"
OUTPUT_DIR = LIGHTGBM_ROOT / "analysis"
FIGURE_DIR = LIGHTGBM_ROOT / "figures"
MODEL_DIR = LIGHTGBM_ROOT / "models"
RANDOM_STATE = 20260720

NUMERIC_FEATURES = [
    "input_tokens",
    "output_tokens",
    "home_cached_prefix_tokens",
    "request_rate_rps",
    "arrival_offset_s",
    "interarrival_ms",
    "global_arrivals_1s",
    "global_arrivals_5s",
    "home_arrivals_1s",
    "home_arrivals_5s",
    "home_workload_share",
    "router_initial_waiting_reqs",
    "router_initial_running_reqs",
    "router_initial_required_kv_bytes",
    "router_initial_available_kv_bytes",
    "router_initial_projected_active_kv_bytes",
    "router_initial_capacity_pressure",
    "router_initial_slot_pressure",
    "router_initial_admissible_candidate_count",
    "router_initial_total_waiting_reqs",
    "router_initial_max_waiting_reqs",
    "router_initial_total_running_reqs",
    "router_initial_max_running_reqs",
    "router_initial_min_available_kv_bytes",
    "router_initial_max_available_kv_bytes",
    "router_initial_min_capacity_pressure",
    "router_initial_max_capacity_pressure",
]
POLICY_FEATURES = [
    "policy_NEAREST_KV",
    "policy_NEAREST_MIGRATE",
    "policy_NEAREST_MIGRATE_KV",
]
FEATURES = NUMERIC_FEATURES + POLICY_FEATURES
COMPONENTS = ("route_event", "route_positive", "scheduler", "compute")

COMMON_PARAMS = {
    "n_estimators": 300,
    "learning_rate": 0.03,
    "num_leaves": 15,
    "max_depth": 4,
    "min_child_samples": 40,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": RANDOM_STATE,
    "n_jobs": -1,
    "verbosity": -1,
}


def prepare_dataset():
    dataset = pd.read_csv(DATASET_PATH)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["output_tokens"] = dataset.output_tokens_actual
    dataset["home_cached_prefix_tokens"] = dataset.nominal_reuse_tokens
    dataset["route_positive"] = (dataset.router_queue_ms > 1e-9).astype(int)
    dataset["communication_ms"] = (
        dataset.kv_transfer_ms + dataset.other_communication_ms
    )
    policy = pd.get_dummies(dataset.policy, prefix="policy", dtype=float)
    for name in POLICY_FEATURES:
        dataset[name] = policy[name] if name in policy else 0.0
    return dataset


def fit_components(train):
    features = train[FEATURES]
    route_event = lgb.LGBMClassifier(
        objective="binary",
        class_weight="balanced",
        **COMMON_PARAMS,
    ).fit(features, train.route_positive)

    positive = train.route_positive.astype(bool)
    route_positive = lgb.LGBMRegressor(
        objective="regression_l1",
        **COMMON_PARAMS,
    ).fit(
        features.loc[positive],
        np.log1p(train.loc[positive, "router_queue_ms"]),
    )
    scheduler = lgb.LGBMRegressor(
        objective="regression_l1",
        **COMMON_PARAMS,
    ).fit(features, train.scheduler_queue_ms)
    compute = lgb.LGBMRegressor(
        objective="regression_l1",
        **COMMON_PARAMS,
    ).fit(features, train.compute_prefill_ms)
    return {
        "route_event": route_event,
        "route_positive": route_positive,
        "scheduler": scheduler,
        "compute": compute,
    }


def predict_components(models, frame):
    features = frame[FEATURES]
    route_probability = models["route_event"].predict_proba(features)[:, 1]
    route_positive_ms = np.expm1(
        models["route_positive"].predict(features)
    ).clip(min=0)
    route_ms = route_probability * route_positive_ms
    scheduler_ms = models["scheduler"].predict(features).clip(min=0)
    compute_ms = models["compute"].predict(features).clip(min=0)
    communication_ms = np.zeros(len(frame))
    return pd.DataFrame({
        "route_probability": route_probability,
        "route_positive_ms": route_positive_ms,
        "predicted_route_ms": route_ms,
        "predicted_scheduler_ms": scheduler_ms,
        "predicted_compute_ms": compute_ms,
        "predicted_communication_ms": communication_ms,
        "predicted_ttft_ms": (
            route_ms + scheduler_ms + compute_ms + communication_ms
        ),
    }, index=frame.index)


def cross_validate(dataset):
    outputs = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        prediction = predict_components(fit_components(train), test)
        prediction.insert(0, "test_scenario", scenario)
        prediction["actual_route_positive"] = test.route_positive.to_numpy()
        prediction["actual_route_ms"] = test.router_queue_ms.to_numpy()
        prediction["actual_scheduler_ms"] = test.scheduler_queue_ms.to_numpy()
        prediction["actual_compute_ms"] = test.compute_prefill_ms.to_numpy()
        prediction["actual_communication_ms"] = test.communication_ms.to_numpy()
        prediction["actual_ttft_ms"] = test.e2e_ttft_ms.to_numpy()
        outputs.append(prediction.reset_index(drop=True))
    return pd.concat(outputs, ignore_index=True)


def calculate_metrics(predictions):
    return {
        "n": len(predictions),
        "n_scenarios": int(predictions.test_scenario.nunique()),
        "route_event_roc_auc": roc_auc_score(
            predictions.actual_route_positive, predictions.route_probability
        ),
        "route_event_pr_auc": average_precision_score(
            predictions.actual_route_positive, predictions.route_probability
        ),
        "route_mae_ms": mean_absolute_error(
            predictions.actual_route_ms, predictions.predicted_route_ms
        ),
        "scheduler_mae_ms": mean_absolute_error(
            predictions.actual_scheduler_ms,
            predictions.predicted_scheduler_ms,
        ),
        "compute_mae_ms": mean_absolute_error(
            predictions.actual_compute_ms, predictions.predicted_compute_ms
        ),
        "communication_omission_mae_ms": mean_absolute_error(
            predictions.actual_communication_ms,
            predictions.predicted_communication_ms,
        ),
        "ttft_mae_ms": mean_absolute_error(
            predictions.actual_ttft_ms, predictions.predicted_ttft_ms
        ),
        "ttft_r2": r2_score(
            predictions.actual_ttft_ms, predictions.predicted_ttft_ms
        ),
    }


def export_importance(models):
    rows = []
    for component in COMPONENTS:
        booster = models[component].booster_
        gain = booster.feature_importance(importance_type="gain")
        split = booster.feature_importance(importance_type="split")
        gain_total = gain.sum()
        split_total = split.sum()
        for feature, gain_value, split_value in zip(FEATURES, gain, split):
            rows.append({
                "component": component,
                "feature": feature,
                "gain": float(gain_value),
                "gain_fraction": (
                    float(gain_value / gain_total) if gain_total else 0.0
                ),
                "split_count": int(split_value),
                "split_fraction": (
                    float(split_value / split_total) if split_total else 0.0
                ),
            })
    importance = pd.DataFrame(rows)
    importance["gain_rank"] = importance.groupby("component").gain.rank(
        method="min", ascending=False
    ).astype(int)
    importance["split_rank"] = importance.groupby("component").split_count.rank(
        method="min", ascending=False
    ).astype(int)
    importance.sort_values(
        ["component", "gain_rank", "feature"]
    ).to_csv(OUTPUT_DIR / "feature_importance.csv", index=False)
    return importance


def comparison_table(lightgbm_metrics):
    baseline = json.loads(BASELINE_SUMMARY.read_text(encoding="utf-8"))
    rows = []
    for metric in (
        "route_event_roc_auc",
        "route_event_pr_auc",
        "route_mae_ms",
        "scheduler_mae_ms",
        "compute_mae_ms",
        "communication_omission_mae_ms",
        "ttft_mae_ms",
        "ttft_r2",
    ):
        old = float(baseline[metric])
        new = float(lightgbm_metrics[metric])
        rows.append({
            "metric": metric,
            "formula_model": old,
            "lightgbm": new,
            "lightgbm_minus_formula": new - old,
        })
    comparison = pd.DataFrame(rows)
    comparison.to_csv(OUTPUT_DIR / "model_comparison.csv", index=False)
    return comparison


def plot_predictions(predictions):
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.5))
    axes[0].scatter(
        predictions.actual_ttft_ms,
        predictions.predicted_ttft_ms,
        s=9,
        alpha=0.25,
        color="#4f83c2",
    )
    limit = max(
        predictions.actual_ttft_ms.max(),
        predictions.predicted_ttft_ms.max(),
    )
    axes[0].plot([0, limit], [0, limit], linestyle="--", color="#202020")
    axes[0].set_xlabel("Actual TTFT (ms)")
    axes[0].set_ylabel("Predicted TTFT (ms)")
    axes[0].set_title("LightGBM scenario-held-out TTFT")
    axes[1].scatter(
        predictions.actual_route_ms,
        predictions.predicted_route_ms,
        s=9,
        alpha=0.25,
        color="#c84f3d",
    )
    axes[1].plot([1, limit], [1, limit], linestyle="--", color="#202020")
    axes[1].set_xscale("symlog", linthresh=1)
    axes[1].set_yscale("symlog", linthresh=1)
    axes[1].set_xlabel("Actual t_route (ms)")
    axes[1].set_ylabel("Predicted t_route (ms)")
    axes[1].set_title("LightGBM router component")
    fig.tight_layout()
    fig.savefig(
        FIGURE_DIR / "ttft_lightgbm_oof.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="#faf8f4",
    )
    plt.close(fig)


def plot_importance(importance):
    fig, axes = plt.subplots(2, 2, figsize=(16, 13))
    for axis, component in zip(axes.flat, COMPONENTS):
        group = importance[importance.component == component].nlargest(
            5, "gain_fraction"
        ).sort_values("gain_fraction")
        axis.barh(group.feature, group.gain_fraction, color="#5576a8")
        axis.set_xlabel("Fraction of total gain")
        axis.set_title(component)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    fig.suptitle("LightGBM feature importance", fontsize=16)
    fig.tight_layout()
    fig.savefig(
        FIGURE_DIR / "feature_importance_gain.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="#faf8f4",
    )
    plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dataset = prepare_dataset()
    predictions = cross_validate(dataset)
    summary = calculate_metrics(predictions)
    final_models = fit_components(dataset)
    bundle = {
        **final_models,
        "features": FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "policy_features": POLICY_FEATURES,
        "communication_point_prediction_ms": 0.0,
    }
    joblib.dump(bundle, MODEL_DIR / "ttft_lightgbm.joblib")
    predictions.to_csv(OUTPUT_DIR / "oof_predictions.csv", index=False)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    importance = export_importance(final_models)
    comparison = comparison_table(summary)
    plot_predictions(predictions)
    plot_importance(importance)
    print(json.dumps(summary, indent=2))
    print()
    print(comparison.to_string(index=False))
    print()
    for component in COMPONENTS:
        top = importance[importance.component == component].nlargest(
            10, "gain_fraction"
        )
        print(f"Top gain importance: {component}")
        print(top[["feature", "gain_fraction", "split_count"]].to_string(index=False))
        print()


if __name__ == "__main__":
    main()
