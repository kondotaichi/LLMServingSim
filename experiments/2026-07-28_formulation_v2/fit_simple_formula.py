#!/usr/bin/env python3
"""Fit the four-term conceptual TTFT formula and generate diagnostics."""

import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score


ROOT = Path(__file__).resolve().parent
DATASET = (
    ROOT.parent / "2026-07-16_ttft_component_regression/analysis/"
    "post_routing/canonical_requests.csv"
)
FIGURE_DIR = ROOT / "figures/simple_formula_fit"
ANALYSIS_DIR = ROOT / "analysis/simple_formula_fit"
MODEL_DIR = ROOT / "models/simple_formula_fit"
N_LOGICAL_INSTANCES = 10


def prepare_dataset():
    frame = pd.read_csv(DATASET)
    frame = frame[frame.has_router_state == 1].copy()
    frame["cached_prefix_tokens"] = frame.nominal_reuse_tokens
    frame["uncached_tokens"] = np.maximum(
        0.0, frame.input_tokens - frame.cached_prefix_tokens
    )

    # phi=(active+required)/budget, hence budget=(active+required)/phi.
    projected = frame.router_initial_projected_active_kv_bytes
    required = frame.router_initial_required_kv_bytes
    budget = (projected + required) / frame.router_initial_capacity_pressure
    frame["kv_deficit_gib"] = np.maximum(
        0.0, projected + required - budget
    ) / (1024.0 ** 3)
    frame["has_kv_deficit"] = (frame.kv_deficit_gib > 0).astype(float)

    # The CSV does not contain exact token work ahead. Approximate waiting
    # requests as prefill-sized work and running requests as decode work.
    frame["waiting_work_tokens"] = (
        frame.router_initial_waiting_reqs * frame.uncached_tokens
    )
    frame["scheduler_constant"] = 1.0
    frame["running_work_units"] = frame.router_initial_running_reqs
    scenario_mean_u = frame.groupby("scenario_id").uncached_tokens.transform("mean")
    frame["arrival_work_kilo_tokens_s"] = (
        frame.request_rate_rps / N_LOGICAL_INSTANCES * scenario_mean_u / 1000.0
    )

    frame["moved_tokens"] = (
        frame.kv_moved * frame.migration_tokens_realized
    )
    frame["computation_constant"] = 1.0
    frame["actual_other_ms"] = frame.other_communication_ms
    return frame


def fit_models(train):
    route_features = train[["has_kv_deficit", "kv_deficit_gib"]]
    route = LinearRegression(fit_intercept=False, positive=True).fit(
        route_features, train.router_queue_ms
    )

    sched_features = train[[
        "scheduler_constant", "waiting_work_tokens", "running_work_units",
        "arrival_work_kilo_tokens_s",
    ]]
    scheduler = LinearRegression(fit_intercept=False, positive=True).fit(
        sched_features, train.scheduler_queue_ms
    )

    computation = LinearRegression(fit_intercept=False, positive=True).fit(
        train[["computation_constant", "uncached_tokens"]],
        train.compute_prefill_ms
    )

    kv_transfer = LinearRegression(fit_intercept=False, positive=True).fit(
        train[["kv_moved", "moved_tokens"]], train.kv_transfer_ms
    )
    return {
        "route": route,
        "scheduler": scheduler,
        "computation": computation,
        "kv_transfer": kv_transfer,
    }


def predict(models, frame):
    route = models["route"].predict(
        frame[["has_kv_deficit", "kv_deficit_gib"]]
    )
    scheduler = models["scheduler"].predict(frame[[
        "scheduler_constant", "waiting_work_tokens", "running_work_units",
        "arrival_work_kilo_tokens_s",
    ]])
    computation = models["computation"].predict(
        frame[["computation_constant", "uncached_tokens"]]
    )
    kv_transfer = models["kv_transfer"].predict(
        frame[["kv_moved", "moved_tokens"]]
    )
    route = np.maximum(0.0, route)
    scheduler = np.maximum(0.0, scheduler)
    computation = np.maximum(0.0, computation)
    kv_transfer = np.maximum(0.0, kv_transfer)
    return pd.DataFrame({
        "predicted_route_ms": route,
        "predicted_scheduler_ms": scheduler,
        "predicted_computation_ms": computation,
        "predicted_kv_transfer_ms": kv_transfer,
        "predicted_ttft_ms": route + scheduler + computation + kv_transfer,
    }, index=frame.index)


def cross_validate(frame):
    outputs = []
    for scenario in sorted(frame.scenario_id.unique()):
        train = frame[frame.scenario_id != scenario]
        test = frame[frame.scenario_id == scenario]
        pred = predict(fit_models(train), test).reset_index(drop=True)
        pred.insert(0, "test_scenario", scenario)
        pred.insert(1, "request_id", test.request_id.to_numpy())
        pred["actual_route_ms"] = test.router_queue_ms.to_numpy()
        pred["actual_scheduler_ms"] = test.scheduler_queue_ms.to_numpy()
        pred["actual_computation_ms"] = test.compute_prefill_ms.to_numpy()
        pred["actual_kv_transfer_ms"] = test.kv_transfer_ms.to_numpy()
        pred["actual_other_ms"] = test.actual_other_ms.to_numpy()
        pred["actual_ttft_ms"] = test.e2e_ttft_ms.to_numpy()
        pred["kv_deficit_gib"] = test.kv_deficit_gib.to_numpy()
        pred["uncached_tokens"] = test.uncached_tokens.to_numpy()
        pred["waiting_work_tokens"] = test.waiting_work_tokens.to_numpy()
        pred["running_work_units"] = test.running_work_units.to_numpy()
        pred["moved_tokens"] = test.moved_tokens.to_numpy()
        outputs.append(pred)
    return pd.concat(outputs, ignore_index=True)


def metrics_row(actual, predicted, target):
    error = np.abs(actual - predicted)
    return {
        "target": target,
        "n": len(actual),
        "mae_ms": float(mean_absolute_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)),
        "abs_error_p50_ms": float(np.quantile(error, 0.50)),
        "abs_error_p90_ms": float(np.quantile(error, 0.90)),
        "abs_error_p95_ms": float(np.quantile(error, 0.95)),
        "abs_error_p99_ms": float(np.quantile(error, 0.99)),
    }


def summarize(predictions):
    rows = []
    for target in (
        "route", "scheduler", "computation", "kv_transfer", "ttft"
    ):
        rows.append(metrics_row(
            predictions[f"actual_{target}_ms"].to_numpy(),
            predictions[f"predicted_{target}_ms"].to_numpy(),
            target,
        ))
    return pd.DataFrame(rows)


def per_scenario_metrics(predictions):
    rows = []
    for scenario, group in predictions.groupby("test_scenario", sort=True):
        row = metrics_row(
            group.actual_ttft_ms.to_numpy(),
            group.predicted_ttft_ms.to_numpy(),
            "ttft",
        )
        row["test_scenario"] = scenario
        rows.append(row)
    return pd.DataFrame(rows)


def export_coefficients(models):
    rows = []
    specs = {
        "route": (["has_kv_deficit", "kv_deficit_gib"], "route_ms"),
        "scheduler": ([
            "scheduler_constant", "waiting_work_tokens", "running_work_units",
            "arrival_work_kilo_tokens_s",
        ], "scheduler_ms"),
        "computation": ([
            "computation_constant", "uncached_tokens"
        ], "computation_ms"),
        "kv_transfer": (["kv_moved", "moved_tokens"], "kv_transfer_ms"),
    }
    for name, (features, target) in specs.items():
        model = models[name]
        rows.append({
            "component": name,
            "term": "intercept",
            "coefficient": float(model.intercept_),
            "target": target,
        })
        rows.extend({
            "component": name,
            "term": feature,
            "coefficient": float(coefficient),
            "target": target,
        } for feature, coefficient in zip(features, model.coef_))
    return pd.DataFrame(rows)


def style_axes(axis):
    axis.grid(True, color="#d7dde5", linewidth=0.7, alpha=0.75)
    axis.spines[["top", "right"]].set_visible(False)


def plot_component_actual_vs_predicted(predictions):
    panels = [
        ("Router queue", "route", "#e45756"),
        ("Scheduler queue", "scheduler", "#f28e2b"),
        ("Computation", "computation", "#4c78a8"),
        ("KV transfer", "kv_transfer", "#59a14f"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 9.0))
    for axis, (title, name, color) in zip(axes.flat, panels):
        actual = predictions[f"actual_{name}_ms"].to_numpy()
        predicted = predictions[f"predicted_{name}_ms"].to_numpy()
        axis.scatter(actual, predicted, s=7, alpha=0.16, color=color,
                     edgecolors="none")
        limit = max(1.0, float(actual.max()), float(predicted.max()))
        axis.plot([0, limit], [0, limit], "--", color="#333333", linewidth=1.4)
        if name in ("route", "kv_transfer"):
            axis.set_xscale("symlog", linthresh=1.0)
            axis.set_yscale("symlog", linthresh=1.0)
        axis.set_xlabel("Actual (ms)")
        axis.set_ylabel("Predicted (ms)")
        axis.set_title(title, fontweight="bold")
        style_axes(axis)
    fig.suptitle("Four-term simple formula: component fits",
                 fontsize=15, fontweight="bold")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "component_actual_vs_predicted.png", dpi=200)
    plt.close(fig)


def plot_ttft_actual_vs_predicted(predictions):
    actual = predictions.actual_ttft_ms.to_numpy() + 1.0
    predicted = predictions.predicted_ttft_ms.to_numpy() + 1.0
    limit = max(float(actual.max()), float(predicted.max()))
    fig, axis = plt.subplots(figsize=(7.2, 6.0))
    density = axis.hexbin(
        actual, predicted, gridsize=60, bins="log", mincnt=1,
        xscale="log", yscale="log", cmap="viridis",
    )
    axis.plot([1, limit], [1, limit], "--", color="#e45756", linewidth=1.8)
    axis.set_xlim(1, limit)
    axis.set_ylim(1, limit)
    axis.set_xlabel("Actual TTFT + 1 ms (log scale)")
    axis.set_ylabel("Predicted TTFT + 1 ms (log scale)")
    axis.set_title("Simple formula: scenario-held-out TTFT",
                   fontweight="bold")
    fig.colorbar(density, ax=axis, label="log10(count)")
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "ttft_actual_vs_predicted.png", dpi=200)
    plt.close(fig)


def plot_error_cdf(predictions):
    fig, axis = plt.subplots(figsize=(7.4, 5.2))
    colors = {
        "TTFT": "#4c78a8", "Router": "#e45756",
        "Scheduler": "#f28e2b", "Computation": "#76b7b2",
        "KV transfer": "#59a14f",
    }
    for label, name in (
        ("TTFT", "ttft"), ("Router", "route"),
        ("Scheduler", "scheduler"), ("Computation", "computation"),
        ("KV transfer", "kv_transfer"),
    ):
        error = np.sort(np.abs(
            predictions[f"actual_{name}_ms"]
            - predictions[f"predicted_{name}_ms"]
        ))
        cdf = np.arange(1, len(error) + 1) / len(error)
        axis.plot(error, cdf, label=label, linewidth=2, color=colors[label])
    axis.set_xscale("symlog", linthresh=1.0)
    axis.set_xlabel("Absolute error (ms, symlog)")
    axis.set_ylabel("CDF")
    axis.set_ylim(0, 1.01)
    axis.set_title("Simple formula: absolute-error CDF", fontweight="bold")
    axis.legend(frameon=False)
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "absolute_error_cdf.png", dpi=200)
    plt.close(fig)


def plot_component_metrics(metrics):
    component = metrics[metrics.target != "ttft"].copy()
    labels = ["Router", "Scheduler", "Computation", "KV transfer"]
    colors = ["#e45756", "#f28e2b", "#4c78a8", "#59a14f"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8))
    axes[0].bar(labels, component.mae_ms, color=colors)
    axes[0].set_ylabel("MAE (ms)")
    axes[0].set_title("Component MAE", fontweight="bold")
    axes[1].bar(labels, component.r2, color=colors)
    axes[1].axhline(0, color="#333333", linewidth=1)
    axes[1].set_ylabel("$R^2$")
    axes[1].set_title("Explained variance", fontweight="bold")
    for axis in axes:
        axis.tick_params(axis="x", rotation=20)
        style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "component_metrics.png", dpi=200)
    plt.close(fig)


def plot_fitted_dependencies(models, frame):
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))

    route_x = np.linspace(0, frame.kv_deficit_gib.quantile(0.995), 180)
    route_grid = pd.DataFrame({
        "has_kv_deficit": (route_x > 0).astype(float),
        "kv_deficit_gib": route_x,
    })
    axes[0, 0].plot(route_x, models["route"].predict(route_grid),
                    color="#e45756", linewidth=2.5)
    axes[0, 0].set_xlabel("KV deficit (GiB)")
    axes[0, 0].set_ylabel("Predicted router queue (ms)")
    axes[0, 0].set_title("KV deficit / recovery-rate proxy", fontweight="bold")

    uncached = np.linspace(frame.uncached_tokens.min(),
                           frame.uncached_tokens.max(), 180)
    axes[0, 1].plot(
        uncached,
        models["computation"].predict(pd.DataFrame({
            "computation_constant": 1.0,
            "uncached_tokens": uncached,
        })),
        color="#4c78a8", linewidth=2.5,
    )
    axes[0, 1].set_xlabel("Uncached input tokens")
    axes[0, 1].set_ylabel("Predicted computation (ms)")
    axes[0, 1].set_title("Uncached tokens / compute throughput", fontweight="bold")

    running = np.linspace(frame.running_work_units.min(),
                          frame.running_work_units.max(), 180)
    sched_grid = pd.DataFrame({
        "scheduler_constant": 1.0,
        "waiting_work_tokens": 0.0,
        "running_work_units": running,
        "arrival_work_kilo_tokens_s": frame.arrival_work_kilo_tokens_s.median(),
    })
    axes[1, 0].plot(running, models["scheduler"].predict(sched_grid),
                    color="#f28e2b", linewidth=2.5)
    axes[1, 0].set_xlabel("Running request work proxy")
    axes[1, 0].set_ylabel("Predicted scheduler queue (ms)")
    axes[1, 0].set_title("Work ahead / backlog drain-rate proxy", fontweight="bold")

    moved = np.linspace(0, frame.moved_tokens.max(), 180)
    kv_grid = pd.DataFrame({
        "kv_moved": (moved > 0).astype(float),
        "moved_tokens": moved,
    })
    axes[1, 1].plot(moved, models["kv_transfer"].predict(kv_grid),
                    color="#59a14f", linewidth=2.5)
    axes[1, 1].set_xlabel("Transferred prefix tokens")
    axes[1, 1].set_ylabel("Predicted KV transfer (ms)")
    axes[1, 1].set_title("KV data / effective bandwidth", fontweight="bold")

    for axis in axes.flat:
        style_axes(axis)
    fig.suptitle("Fitted dependencies of the simple formula",
                 fontsize=15, fontweight="bold")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "fitted_dependencies.png", dpi=200)
    plt.close(fig)


def plot_scenario_mae(scenario_metrics):
    data = scenario_metrics.sort_values("mae_ms")
    labels = [value.split("/")[-1] for value in data.test_scenario]
    fig, axis = plt.subplots(figsize=(10, 6.5))
    median = data.mae_ms.median()
    colors = np.where(data.mae_ms > median, "#e45756", "#4c78a8")
    axis.barh(labels, data.mae_ms, color=colors)
    axis.axvline(median, color="#333333", linestyle="--",
                 label=f"Median = {median:.1f} ms")
    axis.set_xlabel("TTFT MAE (ms)")
    axis.set_title("Simple formula: held-out scenario error", fontweight="bold")
    axis.legend(frameon=False)
    style_axes(axis)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "scenario_ttft_mae.png", dpi=200)
    plt.close(fig)


def main():
    frame = prepare_dataset()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    predictions = cross_validate(frame)
    metrics = summarize(predictions)
    scenario = per_scenario_metrics(predictions)
    final_models = fit_models(frame)
    coefficients = export_coefficients(final_models)

    predictions.to_csv(ANALYSIS_DIR / "oof_predictions.csv", index=False)
    metrics.to_csv(ANALYSIS_DIR / "metrics.csv", index=False)
    scenario.to_csv(ANALYSIS_DIR / "scenario_metrics.csv", index=False)
    coefficients.to_csv(ANALYSIS_DIR / "fitted_coefficients.csv", index=False)
    joblib.dump(final_models, MODEL_DIR / "simple_formula.joblib")
    (ANALYSIS_DIR / "meta.json").write_text(json.dumps({
        "n": len(frame),
        "n_scenarios": int(frame.scenario_id.nunique()),
        "validation": "leave-one-scenario-out",
        "logical_instances_for_arrival_proxy": N_LOGICAL_INSTANCES,
        "route_proxy": "positive KV deficit and deficit GiB",
        "scheduler_proxy": (
            "initial waiting * uncached tokens, initial running requests, "
            "and offered uncached tokens/s per instance"
        ),
        "computation_proxy": "uncached input tokens",
        "kv_transfer_proxy": "realized KV-moved flag and migrated tokens",
        "omitted_from_prediction": "other_communication_ms",
    }, indent=2), encoding="utf-8")

    plot_component_actual_vs_predicted(predictions)
    plot_ttft_actual_vs_predicted(predictions)
    plot_error_cdf(predictions)
    plot_component_metrics(metrics)
    plot_fitted_dependencies(final_models, frame)
    plot_scenario_mae(scenario)
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
