#!/usr/bin/env python3
"""Evaluate router queue prediction using only information known pre-route."""

import importlib.util
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)


ROOT = Path(__file__).resolve().parents[1]
TWO_STAGE_SCRIPT = ROOT / "scripts/analyze_router_queue_two_stage.py"
OUTPUT_DIR = ROOT / "analysis/router_queue_preroute"
FIGURE_DIR = ROOT / "figures/router_queue_preroute"

REQUEST_CONTEXT = [
    "input_tokens",
    "output_tokens_actual",
    "request_rate_rps",
    "arrival_offset_s",
    "interarrival_ms",
    "global_arrivals_1s",
    "global_arrivals_5s",
    "home_arrivals_1s",
    "home_arrivals_5s",
    "home_workload_share",
    "policy",
]

INITIAL_STATE = [
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

FEATURE_SETS = {
    "request_context": REQUEST_CONTEXT,
    "preroute_full": REQUEST_CONTEXT + INITIAL_STATE,
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def predict_fold(module, train, test, columns, feature_set, scenario):
    classifier, regressor = module.fit_models(module.base, train, columns)
    probability, positive_prediction, expected, hard = module.predict_models(
        classifier, regressor, test, columns
    )
    return pd.DataFrame({
        "feature_set": feature_set,
        "test_scenario": scenario,
        "actual_positive": test.queue_positive.to_numpy(),
        "actual_ms": test.router_queue_ms.to_numpy(),
        "probability": probability,
        "positive_prediction_ms": positive_prediction,
        "expected_prediction_ms": expected,
        "hard_prediction_ms": hard,
    })


def aggregate_metrics(frame):
    positive = frame[frame.actual_positive == 1]
    hard_class = frame.probability >= 0.5
    return {
        "n": len(frame),
        "positive_rate": frame.actual_positive.mean(),
        "roc_auc": roc_auc_score(frame.actual_positive, frame.probability),
        "pr_auc": average_precision_score(frame.actual_positive, frame.probability),
        "brier": brier_score_loss(frame.actual_positive, frame.probability),
        "precision_at_0p5": precision_score(frame.actual_positive, hard_class, zero_division=0),
        "recall_at_0p5": recall_score(frame.actual_positive, hard_class, zero_division=0),
        "positive_log_mae": mean_absolute_error(
            np.log1p(positive.actual_ms), np.log1p(positive.positive_prediction_ms)
        ),
        "positive_mae_ms": mean_absolute_error(
            positive.actual_ms, positive.positive_prediction_ms
        ),
        "expected_mae_ms": mean_absolute_error(
            frame.actual_ms, frame.expected_prediction_ms
        ),
        "expected_r2": r2_score(frame.actual_ms, frame.expected_prediction_ms),
        "hard_mae_ms": mean_absolute_error(frame.actual_ms, frame.hard_prediction_ms),
        "hard_r2": r2_score(frame.actual_ms, frame.hard_prediction_ms),
    }


def fold_metrics(predictions):
    rows = []
    for (feature_set, scenario), frame in predictions.groupby(
        ["feature_set", "test_scenario"]
    ):
        positive = frame[frame.actual_positive == 1]
        two_classes = frame.actual_positive.nunique() == 2
        rows.append({
            "feature_set": feature_set,
            "test_scenario": scenario,
            "n": len(frame),
            "n_positive": len(positive),
            "roc_auc": roc_auc_score(frame.actual_positive, frame.probability)
            if two_classes else np.nan,
            "pr_auc": average_precision_score(frame.actual_positive, frame.probability)
            if len(positive) else np.nan,
            "brier": brier_score_loss(frame.actual_positive, frame.probability),
            "positive_log_mae": mean_absolute_error(
                np.log1p(positive.actual_ms), np.log1p(positive.positive_prediction_ms)
            ) if len(positive) else np.nan,
            "expected_mae_ms": mean_absolute_error(
                frame.actual_ms, frame.expected_prediction_ms
            ),
            "expected_r2": r2_score(frame.actual_ms, frame.expected_prediction_ms),
        })
    return pd.DataFrame(rows)


def plot_comparison(metrics):
    labels = {
        "request_context": "Request/context only",
        "preroute_full": "+ Initial router state",
        "postroute_oracle": "Post-route diagnostic",
    }
    panels = [
        ("roc_auc", "ROC-AUC", False),
        ("pr_auc", "PR-AUC", False),
        ("expected_mae_ms", "Composite MAE (ms)", True),
        ("expected_r2", "Composite R²", False),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.8))
    colors = ["#4b79b8", "#358a73", "#9b9b9b"]
    for axis, (metric, title, log_scale) in zip(axes, panels):
        values = metrics.set_index("feature_set").loc[list(labels), metric]
        axis.bar(range(len(values)), values, color=colors)
        axis.set_xticks(range(len(values)), [labels[key] for key in values.index],
                        rotation=25, ha="right")
        axis.set_title(title)
        if log_scale:
            axis.set_yscale("log")
        axis.grid(axis="y", color="#ded8ce")
        axis.set_axisbelow(True)
    fig.suptitle("Router queue prediction: information available at prediction time")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "preroute_model_comparison.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_preroute_predictions(predictions):
    frame = predictions[predictions.feature_set == "preroute_full"]
    positive = frame[frame.actual_positive == 1]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    axes[0].hist(
        [frame.loc[frame.actual_positive == 0, "probability"],
         frame.loc[frame.actual_positive == 1, "probability"]],
        bins=np.linspace(0, 1, 31), label=["No queue", "Queue"],
        color=["#4b79b8", "#c84f3d"], alpha=0.75,
    )
    axes[0].set_xlabel("Predicted queue probability")
    axes[0].set_ylabel("Requests")
    axes[0].set_title("Pre-route occurrence classifier")
    axes[0].legend()
    limit = max(positive.actual_ms.max(), positive.positive_prediction_ms.max())
    axes[1].scatter(positive.actual_ms, positive.positive_prediction_ms,
                    s=10, alpha=0.3, color="#358a73")
    axes[1].plot([1e-3, limit], [1e-3, limit], color="#202020", linestyle="--")
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("Actual positive queue (ms)")
    axes[1].set_ylabel("Predicted positive queue (ms)")
    axes[1].set_title("Pre-route positive-tail regressor")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "preroute_oof_predictions.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    module = load_module(TWO_STAGE_SCRIPT, "two_stage")
    module.base = module.load_base_module()
    dataset = pd.read_csv(module.BASE_DATASET)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["queue_positive"] = (dataset.router_queue_ms > 1e-9).astype(int)

    prediction_frames = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        for feature_set, columns in FEATURE_SETS.items():
            prediction_frames.append(
                predict_fold(module, train, test, columns, feature_set, scenario)
            )
    predictions = pd.concat(prediction_frames, ignore_index=True)
    folds = fold_metrics(predictions)
    metric_rows = []
    for feature_set, frame in predictions.groupby("feature_set"):
        metric_rows.append({"feature_set": feature_set, **aggregate_metrics(frame)})

    postroute = json.loads(
        (ROOT / "analysis/router_queue_two_stage/summary.json").read_text()
    )
    metric_rows.append({
        "feature_set": "postroute_oracle",
        **{key: postroute.get(key, np.nan) for key in metric_rows[0] if key != "feature_set"},
    })
    metrics = pd.DataFrame(metric_rows)
    predictions.to_csv(OUTPUT_DIR / "oof_predictions.csv", index=False)
    folds.to_csv(OUTPUT_DIR / "fold_metrics.csv", index=False)
    metrics.to_csv(OUTPUT_DIR / "model_comparison.csv", index=False)
    plot_comparison(metrics)
    plot_preroute_predictions(predictions)
    summary = {
        row["feature_set"]: {
            key: float(value) if pd.notna(value) else None
            for key, value in row.items() if key != "feature_set"
        }
        for row in metric_rows
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
