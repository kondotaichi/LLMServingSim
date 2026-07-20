#!/usr/bin/env python3
"""Measure individual input importance for the router queue two-stage model."""

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error


ROOT = Path(__file__).resolve().parents[1]
TWO_STAGE_SCRIPT = ROOT / "scripts/analyze_router_queue_two_stage.py"
OUTPUT_DIR = ROOT / "analysis/router_queue_two_stage"
FIGURE_DIR = ROOT / "figures/router_queue_two_stage"
RNG_SEED = 20260720

# Deterministic transforms or thresholded versions of other model inputs.
DERIVED_FEATURES = {
    "minimum_prefill_chunks",
    "nominal_reuse_ratio",
    "reuse_preservation_ratio",
    "reuse_lost",
    "realized_uncached_tokens",
    "offered_uncached_tokens_per_s",
    "router_initial_available_kv_bytes",
    "router_initial_capacity_pressure",
    "router_initial_slot_pressure",
    "router_initial_min_capacity_pressure",
    "router_initial_max_capacity_pressure",
    "router_decision_available_kv_bytes",
    "router_decision_capacity_pressure",
    "router_decision_slot_pressure",
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def losses(module, train, test, columns):
    classifier, regressor = module.fit_models(module.base, train, columns)
    probability, positive_prediction, expected, _ = module.predict_models(
        classifier, regressor, test, columns
    )
    logloss = -np.mean(
        test.queue_positive * np.log(np.clip(probability, 1e-12, 1))
        + (1 - test.queue_positive) * np.log(np.clip(1 - probability, 1e-12, 1))
    )
    positive_mask = test.queue_positive.to_numpy(dtype=bool)
    positive_log_mae = mean_absolute_error(
        np.log1p(test.loc[positive_mask, "router_queue_ms"]),
        np.log1p(positive_prediction[positive_mask]),
    ) if positive_mask.any() else np.nan
    composite_mae = mean_absolute_error(test.router_queue_ms, expected)
    return logloss, positive_log_mae, composite_mae


def bootstrap(frame, repetitions=2000):
    rng = np.random.default_rng(RNG_SEED)
    metrics = [
        "classifier_logloss_increase",
        "positive_log_mae_increase",
        "composite_mae_increase_ms",
    ]
    rows = []
    for feature, feature_frame in frame.groupby("feature"):
        scenarios = feature_frame.test_scenario.unique()
        for metric in metrics:
            values = feature_frame.set_index("test_scenario")[metric]
            samples = np.array([
                values.loc[rng.choice(scenarios, len(scenarios), replace=True)].mean()
                for _ in range(repetitions)
            ])
            rows.append({
                "feature": feature,
                "feature_kind": "derived" if feature in DERIVED_FEATURES else "direct_or_aggregate",
                "metric": metric,
                "mean": feature_frame[metric].mean(),
                "ci95_low": np.quantile(samples, 0.025),
                "ci95_high": np.quantile(samples, 0.975),
                "n_scenarios": len(scenarios),
            })
    return pd.DataFrame(rows)


def plot_importance(summary):
    specifications = [
        ("classifier_logloss_increase", "Queue occurrence", "Log-loss increase"),
        ("positive_log_mae_increase", "Positive-tail duration", "log1p(queue) MAE increase"),
        ("composite_mae_increase_ms", "Composite t_route", "MAE increase (ms)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(19, 8))
    for axis, (metric, title, xlabel) in zip(axes, specifications):
        frame = summary[summary.metric == metric].sort_values("mean").tail(15)
        values = frame["mean"].to_numpy()
        errors = np.vstack([
            values - frame.ci95_low.to_numpy(),
            frame.ci95_high.to_numpy() - values,
        ])
        colors = [
            "#8456d8" if kind == "derived" else "#358a73"
            for kind in frame.feature_kind
        ]
        axis.barh(frame.feature, values, color=colors)
        axis.errorbar(values, range(len(frame)), xerr=errors, fmt="none",
                      ecolor="#202020", capsize=3)
        axis.axvline(0, color="#202020", linewidth=0.8)
        axis.set_title(title)
        axis.set_xlabel(xlabel + "\n95% scenario-bootstrap CI")
        axis.grid(axis="x", color="#ded8ce")
        axis.set_axisbelow(True)
    fig.suptitle("Individual drop-column importance (purple = derived input)", fontsize=14)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "individual_feature_importance.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    global base
    module = load_module(TWO_STAGE_SCRIPT, "two_stage")
    base = module.load_base_module()
    module.base = base
    dataset = pd.read_csv(module.BASE_DATASET)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["queue_positive"] = (dataset.router_queue_ms > 1e-9).astype(int)
    dataset, groups = base.prepare_subset(dataset, base.STATE_GROUPS)
    columns = base.model_columns(groups)
    scenarios = sorted(dataset.scenario_id.unique())

    rows = []
    raw_model_rows = []
    raw_columns = [column for column in columns if column not in DERIVED_FEATURES]
    for fold, scenario in enumerate(scenarios):
        print(f"fold {fold + 1}/{len(scenarios)}: {scenario}", flush=True)
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        full_losses = losses(module, train, test, columns)
        raw_losses = losses(module, train, test, raw_columns)
        raw_model_rows.append({
            "test_scenario": scenario,
            "full_classifier_logloss": full_losses[0],
            "raw_classifier_logloss": raw_losses[0],
            "full_positive_log_mae": full_losses[1],
            "raw_positive_log_mae": raw_losses[1],
            "full_composite_mae_ms": full_losses[2],
            "raw_composite_mae_ms": raw_losses[2],
        })
        for index, feature in enumerate(columns):
            if index % 10 == 0:
                print(f"  feature {index + 1}/{len(columns)}", flush=True)
            reduced = [column for column in columns if column != feature]
            reduced_losses = losses(module, train, test, reduced)
            rows.append({
                "test_scenario": scenario,
                "feature": feature,
                "classifier_logloss_increase": reduced_losses[0] - full_losses[0],
                "positive_log_mae_increase": reduced_losses[1] - full_losses[1],
                "composite_mae_increase_ms": reduced_losses[2] - full_losses[2],
            })

    ablation = pd.DataFrame(rows)
    summary = bootstrap(ablation)
    raw_comparison = pd.DataFrame(raw_model_rows)
    ablation.to_csv(OUTPUT_DIR / "individual_feature_ablation.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "bootstrap_individual_feature_importance.csv", index=False)
    raw_comparison.to_csv(OUTPUT_DIR / "raw_vs_all_features.csv", index=False)
    plot_importance(summary)
    print("\nTop individual features", flush=True)
    print(summary.sort_values(["metric", "mean"], ascending=[True, False])
          .groupby("metric").head(10).to_string(index=False), flush=True)
    print("\nRaw minus full mean losses", flush=True)
    print(pd.Series({
        "classifier_logloss": (raw_comparison.raw_classifier_logloss
                               - raw_comparison.full_classifier_logloss).mean(),
        "positive_log_mae": (raw_comparison.raw_positive_log_mae
                             - raw_comparison.full_positive_log_mae).mean(),
        "composite_mae_ms": (raw_comparison.raw_composite_mae_ms
                             - raw_comparison.full_composite_mae_ms).mean(),
    }).to_string(), flush=True)


if __name__ == "__main__":
    main()
