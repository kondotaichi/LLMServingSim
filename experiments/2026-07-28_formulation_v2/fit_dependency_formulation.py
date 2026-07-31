#!/usr/bin/env python3
"""Fit and visualize the dependency-based TTFT formulation."""

import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, SplineTransformer, StandardScaler


ROOT = Path(__file__).resolve().parent
DATASET = (
    ROOT.parent / "2026-07-16_ttft_component_regression/analysis/"
    "post_routing/canonical_requests.csv"
)
ANALYSIS_DIR = ROOT / "analysis/dependency_fit"
FIGURE_DIR = ROOT / "figures/dependency_fit"
MODEL_DIR = ROOT / "models/dependency_fit"

ROUTE_FEATURES = [
    "log1p_retry_count",
    "router_initial_capacity_pressure",
    "router_initial_admissible_candidate_count",
]
QUEUE_FEATURES = [
    "request_rate_rps",
    "router_initial_waiting_reqs",
    "router_initial_running_reqs",
    "router_initial_capacity_pressure",
    "router_initial_admissible_candidate_count",
]
PREFILL_FEATURES = ["uncached_tokens", "cached_prefix_tokens"]
COMM_FEATURES = ["rerouted", "kv_moved", "migrated_tokens"]


def prepare_dataset():
    frame = pd.read_csv(DATASET)
    frame = frame[frame.has_router_state == 1].copy()
    frame["cached_prefix_tokens"] = frame.nominal_reuse_tokens
    frame["uncached_tokens"] = np.maximum(
        0.0, frame.input_tokens - frame.cached_prefix_tokens
    )
    frame["wait_ms"] = frame.router_queue_ms + frame.scheduler_queue_ms
    frame["log1p_retry_count"] = np.log1p(
        frame.router_capacity_retry_count
    )
    frame["communication_ms"] = (
        frame.kv_transfer_ms + frame.other_communication_ms
    )
    frame["migrated_tokens"] = (
        frame.kv_moved * frame.migration_tokens_realized
    )
    return frame


def make_spline_model(features, alpha):
    transformer = ColumnTransformer([
        (name, Pipeline([
            ("spline", SplineTransformer(
                n_knots=5, degree=3, include_bias=False,
                extrapolation="linear",
            )),
            ("scale", StandardScaler()),
        ]), [name])
        for name in features
    ])
    return Pipeline([
        ("features", transformer),
        ("ridge", Ridge(alpha=alpha)),
    ])


def fit_models(train):
    route = make_spline_model(ROUTE_FEATURES, alpha=30.0)
    route.fit(train[ROUTE_FEATURES], np.log1p(train.router_queue_ms))

    queue = make_spline_model(QUEUE_FEATURES, alpha=100.0)
    queue.fit(train[QUEUE_FEATURES], np.log1p(train.scheduler_queue_ms))

    prefill = make_spline_model(PREFILL_FEATURES, alpha=10.0)
    prefill.fit(
        train[PREFILL_FEATURES], np.log1p(train.compute_prefill_ms)
    )

    communication = LinearRegression(positive=True)
    communication.fit(train[COMM_FEATURES], train.communication_ms)
    return {
        "route": route, "queue": queue, "prefill": prefill,
        "communication": communication,
    }


def predict(models, frame):
    route_ms = np.expm1(models["route"].predict(frame[ROUTE_FEATURES]))
    queue_ms = np.expm1(models["queue"].predict(frame[QUEUE_FEATURES]))
    prefill_ms = np.expm1(models["prefill"].predict(frame[PREFILL_FEATURES]))
    communication_ms = models["communication"].predict(frame[COMM_FEATURES])
    route_ms = np.maximum(0.0, route_ms)
    queue_ms = np.maximum(0.0, queue_ms)
    wait_ms = route_ms + queue_ms
    prefill_ms = np.maximum(0.0, prefill_ms)
    communication_ms = np.maximum(0.0, communication_ms)
    return pd.DataFrame({
        "predicted_route_ms": route_ms,
        "predicted_queue_ms": queue_ms,
        "predicted_wait_ms": wait_ms,
        "predicted_prefill_ms": prefill_ms,
        "predicted_communication_ms": communication_ms,
        "predicted_ttft_ms": wait_ms + prefill_ms + communication_ms,
    }, index=frame.index)


def cross_validate(frame):
    outputs = []
    for scenario in sorted(frame.scenario_id.unique()):
        train = frame[frame.scenario_id != scenario]
        test = frame[frame.scenario_id == scenario]
        prediction = predict(fit_models(train), test).reset_index(drop=True)
        prediction.insert(0, "test_scenario", scenario)
        prediction.insert(1, "request_id", test.request_id.to_numpy())
        prediction["actual_wait_ms"] = test.wait_ms.to_numpy()
        prediction["actual_prefill_ms"] = test.compute_prefill_ms.to_numpy()
        prediction["actual_communication_ms"] = test.communication_ms.to_numpy()
        prediction["actual_ttft_ms"] = test.e2e_ttft_ms.to_numpy()
        outputs.append(prediction)
    return pd.concat(outputs, ignore_index=True)


def regression_metrics(actual, predicted, prefix):
    error = np.abs(actual - predicted)
    return {
        "target": prefix,
        "mae_ms": float(mean_absolute_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)),
        "abs_error_p50_ms": float(np.quantile(error, 0.50)),
        "abs_error_p90_ms": float(np.quantile(error, 0.90)),
        "abs_error_p95_ms": float(np.quantile(error, 0.95)),
        "abs_error_p99_ms": float(np.quantile(error, 0.99)),
    }


def summarize(predictions):
    rows = []
    for name in ("wait", "prefill", "communication", "ttft"):
        rows.append(regression_metrics(
            predictions[f"actual_{name}_ms"].to_numpy(),
            predictions[f"predicted_{name}_ms"].to_numpy(),
            name,
        ))
    return pd.DataFrame(rows)


def scenario_metrics(predictions):
    rows = []
    for scenario, group in predictions.groupby("test_scenario", sort=True):
        row = regression_metrics(
            group.actual_ttft_ms.to_numpy(),
            group.predicted_ttft_ms.to_numpy(),
            "ttft",
        )
        row["test_scenario"] = scenario
        rows.append(row)
    return pd.DataFrame(rows)


def style_axes(axis):
    axis.grid(True, color="#d7dde5", linewidth=0.7, alpha=0.75)
    axis.spines[["top", "right"]].set_visible(False)


def plot_actual_vs_predicted(predictions):
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2))
    panels = [
        ("TTFT", predictions.actual_ttft_ms, predictions.predicted_ttft_ms),
        ("Wait (route + scheduler)",
         predictions.actual_wait_ms, predictions.predicted_wait_ms),
    ]
    for axis, (title, actual, predicted) in zip(axes, panels):
        actual_plot = np.asarray(actual) + 1.0
        predicted_plot = np.asarray(predicted) + 1.0
        limit = max(float(actual_plot.max()), float(predicted_plot.max()))
        axis.hexbin(
            actual_plot, predicted_plot, gridsize=55, bins="log", mincnt=1,
            cmap="viridis", xscale="log", yscale="log",
        )
        axis.plot([1, limit], [1, limit], "--", color="#e45756", linewidth=1.8)
        axis.set_xlim(1, limit)
        axis.set_ylim(1, limit)
        axis.set_xlabel("Actual time + 1 ms (log scale)")
        axis.set_ylabel("Predicted time + 1 ms (log scale)")
        axis.set_title(title, fontweight="bold")
        style_axes(axis)
    fig.suptitle(
        "Dependency formulation: scenario-held-out predictions",
        fontsize=15, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "actual_vs_predicted.png", dpi=200)
    plt.close(fig)


def plot_error_cdf(predictions):
    fig, axis = plt.subplots(figsize=(7.4, 5.0))
    colors = {
        "TTFT": "#4c78a8", "Wait": "#e45756",
        "Prefill": "#59a14f", "Communication": "#b279a2",
    }
    for label, name in (
        ("TTFT", "ttft"), ("Wait", "wait"),
        ("Prefill", "prefill"), ("Communication", "communication"),
    ):
        error = np.sort(np.abs(
            predictions[f"actual_{name}_ms"]
            - predictions[f"predicted_{name}_ms"]
        ))
        cdf = np.arange(1, len(error) + 1) / len(error)
        axis.plot(error, cdf, label=label, color=colors[label], linewidth=2)
    axis.set_xscale("symlog", linthresh=1.0)
    axis.set_xlabel("Absolute error (ms, symlog)")
    axis.set_ylabel("CDF")
    axis.set_ylim(0, 1.01)
    axis.legend(frameon=False)
    axis.set_title("Out-of-scenario error distribution", fontweight="bold")
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "absolute_error_cdf.png", dpi=200)
    plt.close(fig)


def plot_scenario_mae(metrics):
    data = metrics.sort_values("mae_ms")
    labels = [value.split("/")[-1] for value in data.test_scenario]
    fig, axis = plt.subplots(figsize=(10, 6.5))
    colors = np.where(data.mae_ms > data.mae_ms.median(), "#e45756", "#4c78a8")
    axis.barh(labels, data.mae_ms, color=colors)
    axis.axvline(data.mae_ms.median(), color="#333333", linestyle="--",
                 label=f"Median = {data.mae_ms.median():.1f} ms")
    axis.set_xlabel("TTFT MAE (ms)")
    axis.set_title("Generalization error by held-out scenario", fontweight="bold")
    axis.legend(frameon=False)
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "scenario_ttft_mae.png", dpi=200)
    plt.close(fig)


def plot_dependency_curves(models, frame):
    specs = [
        ("router_initial_capacity_pressure", "Capacity pressure", "#e45756"),
        ("router_initial_running_reqs", "Running requests", "#4c78a8"),
        ("request_rate_rps", "Arrival rate (requests/s)", "#f28e2b"),
        ("router_initial_admissible_candidate_count", "Admissible GPUs", "#59a14f"),
    ]
    baseline = frame[QUEUE_FEATURES].median().to_frame().T
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0))
    for axis, (feature, label, color) in zip(axes.flat, specs):
        low, high = frame[feature].quantile([0.01, 0.99])
        values = np.linspace(low, high, 120)
        grid = pd.concat([baseline] * len(values), ignore_index=True)
        grid[feature] = values
        prediction = np.maximum(
            0.0, np.expm1(models["queue"].predict(grid[QUEUE_FEATURES]))
        )
        axis.plot(values, prediction, color=color, linewidth=2.5)
        axis.set_xlabel(label)
        axis.set_ylabel("Predicted scheduler queue (ms)")
        style_axes(axis)
    fig.suptitle(
        "Fitted scheduler dependencies (other variables at median)",
        fontsize=15, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "wait_dependency_curves.png", dpi=200)
    plt.close(fig)


def plot_route_retry_curve(models, frame):
    baseline = frame[ROUTE_FEATURES].median().to_frame().T
    positive = frame.loc[frame.router_capacity_retry_count > 0,
                         "router_capacity_retry_count"]
    values = np.geomspace(1.0, positive.quantile(0.99), 180)
    grid = pd.concat([baseline] * len(values), ignore_index=True)
    grid["log1p_retry_count"] = np.log1p(values)
    predicted = np.maximum(
        0.0, np.expm1(models["route"].predict(grid[ROUTE_FEATURES]))
    )
    fig, axis = plt.subplots(figsize=(7.5, 5.0))
    axis.plot(values, predicted, color="#e45756", linewidth=2.5)
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlabel("Capacity retry count (log scale)")
    axis.set_ylabel("Predicted routing wait (ms, log scale)")
    axis.set_title("Fitted routing-retry dependency", fontweight="bold")
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "route_retry_dependency.png", dpi=200)
    plt.close(fig)


def plot_prefill_surface(models, frame):
    fig, axis = plt.subplots(figsize=(7.5, 5.0))
    colors = ["#4c78a8", "#f28e2b", "#59a14f"]
    cache_levels = sorted(frame.cached_prefix_tokens.unique())
    if len(cache_levels) > 3:
        cache_levels = np.quantile(cache_levels, [0, 0.5, 1])
    for cache, color in zip(cache_levels, colors):
        support = frame[np.isclose(frame.cached_prefix_tokens, cache)]
        if support.empty:
            nearest = (frame.cached_prefix_tokens - cache).abs().idxmin()
            cache = float(frame.loc[nearest, "cached_prefix_tokens"])
            support = frame[np.isclose(frame.cached_prefix_tokens, cache)]
        uncached = np.linspace(
            support.uncached_tokens.min(), support.uncached_tokens.max(), 150,
        )
        grid = pd.DataFrame({
            "uncached_tokens": uncached,
            "cached_prefix_tokens": float(cache),
        })
        predicted = np.expm1(
            models["prefill"].predict(grid[PREFILL_FEATURES])
        )
        axis.plot(
            uncached, predicted, color=color, linewidth=2.5,
            marker="o", markevery=max(1, len(uncached) // 6), markersize=4,
            label=f"Cached prefix = {cache:.0f} tokens",
        )
    axis.set_xlabel("Uncached input tokens")
    axis.set_ylabel("Predicted prefill time (ms)")
    axis.set_title("Fitted prefill dependency", fontweight="bold")
    axis.legend(frameon=False)
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "prefill_dependency.png", dpi=200)
    plt.close(fig)


def main():
    frame = prepare_dataset()
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    predictions = cross_validate(frame)
    metrics = summarize(predictions)
    per_scenario = scenario_metrics(predictions)
    final_models = fit_models(frame)

    predictions.to_csv(ANALYSIS_DIR / "oof_predictions.csv", index=False)
    metrics.to_csv(ANALYSIS_DIR / "metrics.csv", index=False)
    per_scenario.to_csv(ANALYSIS_DIR / "scenario_metrics.csv", index=False)
    joblib.dump(final_models, MODEL_DIR / "dependency_formulation.joblib")
    (ANALYSIS_DIR / "meta.json").write_text(json.dumps({
        "n": len(frame),
        "n_scenarios": int(frame.scenario_id.nunique()),
        "validation": "leave-one-scenario-out",
        "route_target": "log1p(router_queue_ms)",
        "route_features": ROUTE_FEATURES,
        "queue_target": "log1p(scheduler_queue_ms)",
        "queue_features": QUEUE_FEATURES,
        "prefill_target": "log1p(compute_prefill_ms)",
        "prefill_features": PREFILL_FEATURES,
        "communication_target": "kv_transfer_ms + other_communication_ms",
        "communication_features": COMM_FEATURES,
        "note": (
            "Communication features describe the realized routing action and "
            "are explanatory rather than send-time predictors."
        ),
    }, indent=2), encoding="utf-8")

    plot_actual_vs_predicted(predictions)
    plot_error_cdf(predictions)
    plot_scenario_mae(per_scenario)
    plot_dependency_curves(final_models, frame)
    plot_route_retry_curve(final_models, frame)
    plot_prefill_surface(final_models, frame)
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
