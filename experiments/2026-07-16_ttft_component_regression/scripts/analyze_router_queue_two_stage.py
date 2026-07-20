#!/usr/bin/env python3
"""Fit a two-stage model for zero-inflated router queue latency."""

import importlib.util
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    precision_score,
    recall_score,
    r2_score,
    roc_auc_score,
)


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "analysis/router_queue_two_stage"
FIGURE_DIR = ROOT / "figures/router_queue_two_stage"
BASE_SCRIPT = ROOT / "scripts/analyze_post_routing_all.py"
BASE_DATASET = ROOT / "analysis/post_routing/canonical_requests.csv"
RNG_SEED = 20260720


def load_base_module():
    spec = importlib.util.spec_from_file_location("post_routing", BASE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fit_models(base, train, columns):
    classifier = base.make_model(columns)
    classifier.steps[-1] = (
        "model",
        LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000),
    )
    classifier.fit(train[columns], train.queue_positive)

    positive = train[train.queue_positive == 1]
    regressor = base.make_model(columns)
    regressor.steps[-1] = (
        "model",
        HistGradientBoostingRegressor(
            loss="absolute_error", max_iter=200, max_leaf_nodes=15,
            min_samples_leaf=20, l2_regularization=1.0, random_state=RNG_SEED,
        ),
    )
    positive_log_queue = np.log1p(positive.router_queue_ms)
    regressor.fit(positive[columns], positive_log_queue)
    regressor.positive_log_bounds_ = (
        float(positive_log_queue.min()), float(positive_log_queue.max())
    )
    return classifier, regressor


def predict_models(classifier, regressor, frame, columns):
    probability = classifier.predict_proba(frame[columns])[:, 1]
    log_prediction = np.clip(
        regressor.predict(frame[columns]), *regressor.positive_log_bounds_
    )
    positive_prediction = np.expm1(log_prediction).clip(min=0)
    expected_prediction = probability * positive_prediction
    hard_prediction = np.where(probability >= 0.5, positive_prediction, 0.0)
    return probability, positive_prediction, expected_prediction, hard_prediction


def fold_metrics(predictions):
    rows = []
    for scenario, frame in predictions.groupby("test_scenario"):
        positive = frame[frame.actual_positive == 1]
        rows.append({
            "test_scenario": scenario,
            "n": len(frame),
            "n_positive": len(positive),
            "positive_rate": frame.actual_positive.mean(),
            "roc_auc": roc_auc_score(frame.actual_positive, frame.probability)
            if frame.actual_positive.nunique() == 2 else np.nan,
            "pr_auc": average_precision_score(frame.actual_positive, frame.probability)
            if frame.actual_positive.sum() else np.nan,
            "brier": brier_score_loss(frame.actual_positive, frame.probability),
            "precision_at_0p5": precision_score(
                frame.actual_positive, frame.probability >= 0.5, zero_division=0
            ),
            "recall_at_0p5": recall_score(
                frame.actual_positive, frame.probability >= 0.5, zero_division=0
            ),
            "positive_log_mae": mean_absolute_error(
                np.log1p(positive.actual_ms), np.log1p(positive.positive_prediction_ms)
            ) if len(positive) else np.nan,
            "positive_mae_ms": mean_absolute_error(
                positive.actual_ms, positive.positive_prediction_ms
            ) if len(positive) else np.nan,
            "expected_mae_ms": mean_absolute_error(
                frame.actual_ms, frame.expected_prediction_ms
            ),
            "hard_mae_ms": mean_absolute_error(frame.actual_ms, frame.hard_prediction_ms),
            "expected_r2": r2_score(frame.actual_ms, frame.expected_prediction_ms),
            "hard_r2": r2_score(frame.actual_ms, frame.hard_prediction_ms),
        })
    return pd.DataFrame(rows)


def ablation_rows(base, dataset, groups):
    full_columns = base.model_columns(groups)
    rows = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        full_classifier, full_regressor = fit_models(base, train, full_columns)
        full = predict_models(full_classifier, full_regressor, test, full_columns)
        full_logloss = -np.mean(
            test.queue_positive * np.log(np.clip(full[0], 1e-12, 1))
            + (1 - test.queue_positive) * np.log(np.clip(1 - full[0], 1e-12, 1))
        )
        positive_mask = test.queue_positive.to_numpy(dtype=bool)
        full_log_mae = mean_absolute_error(
            np.log1p(test.loc[positive_mask, "router_queue_ms"]),
            np.log1p(full[1][positive_mask]),
        ) if positive_mask.any() else np.nan
        full_composite_mae = mean_absolute_error(test.router_queue_ms, full[2])
        for group, removed in groups.items():
            columns = [column for column in full_columns if column not in removed]
            classifier, regressor = fit_models(base, train, columns)
            prediction = predict_models(classifier, regressor, test, columns)
            logloss = -np.mean(
                test.queue_positive * np.log(np.clip(prediction[0], 1e-12, 1))
                + (1 - test.queue_positive) * np.log(np.clip(1 - prediction[0], 1e-12, 1))
            )
            log_mae = mean_absolute_error(
                np.log1p(test.loc[positive_mask, "router_queue_ms"]),
                np.log1p(prediction[1][positive_mask]),
            ) if positive_mask.any() else np.nan
            composite_mae = mean_absolute_error(test.router_queue_ms, prediction[2])
            rows.append({
                "test_scenario": scenario,
                "group": group,
                "classifier_logloss_increase": logloss - full_logloss,
                "positive_log_mae_increase": log_mae - full_log_mae,
                "composite_mae_increase_ms": composite_mae - full_composite_mae,
            })
    return pd.DataFrame(rows)


def bootstrap_ablation(ablation, repetitions=2000):
    rng = np.random.default_rng(RNG_SEED)
    metrics = [
        "classifier_logloss_increase",
        "positive_log_mae_increase",
        "composite_mae_increase_ms",
    ]
    rows = []
    for group, frame in ablation.groupby("group"):
        scenarios = frame.test_scenario.unique()
        for metric in metrics:
            scenario_values = frame.set_index("test_scenario")[metric]
            samples = np.array([
                scenario_values.loc[
                    rng.choice(scenarios, size=len(scenarios), replace=True)
                ].mean()
                for _ in range(repetitions)
            ])
            rows.append({
                "group": group,
                "metric": metric,
                "mean": frame[metric].mean(),
                "ci95_low": np.quantile(samples, 0.025),
                "ci95_high": np.quantile(samples, 0.975),
                "n_scenarios": len(scenarios),
            })
    return pd.DataFrame(rows)


def plot_ablation(bootstrap):
    specifications = [
        ("classifier_logloss_increase", "Queue occurrence classifier", "Log-loss increase"),
        ("positive_log_mae_increase", "Positive-tail regressor", "log1p(queue) MAE increase"),
        ("composite_mae_increase_ms", "Two-stage expected queue", "MAE increase (ms)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(17, 7))
    for axis, (metric, title, xlabel) in zip(axes, specifications):
        frame = bootstrap[bootstrap.metric == metric].sort_values("mean")
        values = frame["mean"].to_numpy()
        errors = np.vstack([
            values - frame.ci95_low.to_numpy(),
            frame.ci95_high.to_numpy() - values,
        ])
        colors = np.where(frame.ci95_low > 0, "#26734d", "#9b9b9b")
        axis.barh(frame.group, values, color=colors)
        axis.errorbar(values, range(len(frame)), xerr=errors, fmt="none",
                      ecolor="#202020", capsize=3)
        axis.axvline(0, color="#202020", linewidth=0.8)
        axis.set_title(title)
        axis.set_xlabel(xlabel + "\n95% scenario-bootstrap CI")
        axis.grid(axis="x", color="#ded8ce")
        axis.set_axisbelow(True)
    fig.suptitle("Router queue two-stage feature-group importance", fontsize=14)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "two_stage_group_importance.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_predictions(predictions):
    positive = predictions[predictions.actual_positive == 1]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    axes[0].hist(
        [predictions.loc[predictions.actual_positive == 0, "probability"],
         predictions.loc[predictions.actual_positive == 1, "probability"]],
        bins=np.linspace(0, 1, 31), label=["No queue", "Queue"],
        color=["#4b79b8", "#c84f3d"], alpha=0.75,
    )
    axes[0].set_xlabel("Predicted queue probability")
    axes[0].set_ylabel("Requests")
    axes[0].set_title("Occurrence classifier separation")
    axes[0].legend()
    limit = max(positive.actual_ms.max(), positive.positive_prediction_ms.max())
    axes[1].scatter(positive.actual_ms, positive.positive_prediction_ms,
                    s=10, alpha=0.3, color="#358a73")
    axes[1].plot([1, limit], [1, limit], color="#202020", linestyle="--")
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("Actual positive queue (ms)")
    axes[1].set_ylabel("Predicted positive queue (ms)")
    axes[1].set_title("Positive-tail regressor")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "two_stage_oof_predictions.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base_module()
    dataset = pd.read_csv(BASE_DATASET)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["queue_positive"] = (dataset.router_queue_ms > 1e-9).astype(int)
    dataset, groups = base.prepare_subset(dataset, base.STATE_GROUPS)
    columns = base.model_columns(groups)

    prediction_frames = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        classifier, regressor = fit_models(base, train, columns)
        probability, positive_prediction, expected, hard = predict_models(
            classifier, regressor, test, columns
        )
        prediction_frames.append(pd.DataFrame({
            "test_scenario": scenario,
            "actual_positive": test.queue_positive.to_numpy(),
            "actual_ms": test.router_queue_ms.to_numpy(),
            "probability": probability,
            "positive_prediction_ms": positive_prediction,
            "expected_prediction_ms": expected,
            "hard_prediction_ms": hard,
        }))
    predictions = pd.concat(prediction_frames, ignore_index=True)
    metrics = fold_metrics(predictions)
    ablation = ablation_rows(base, dataset, groups)
    bootstrap = bootstrap_ablation(ablation)

    predictions.to_csv(ANALYSIS_DIR / "oof_predictions.csv", index=False)
    metrics.to_csv(ANALYSIS_DIR / "fold_metrics.csv", index=False)
    ablation.to_csv(ANALYSIS_DIR / "group_ablation.csv", index=False)
    bootstrap.to_csv(ANALYSIS_DIR / "bootstrap_group_importance.csv", index=False)
    plot_ablation(bootstrap)
    plot_predictions(predictions)

    positive = predictions[predictions.actual_positive == 1]
    summary = {
        "n": len(predictions),
        "n_scenarios": int(predictions.test_scenario.nunique()),
        "n_positive": int(predictions.actual_positive.sum()),
        "positive_rate": float(predictions.actual_positive.mean()),
        "roc_auc": float(roc_auc_score(predictions.actual_positive, predictions.probability)),
        "pr_auc": float(average_precision_score(predictions.actual_positive, predictions.probability)),
        "brier": float(brier_score_loss(predictions.actual_positive, predictions.probability)),
        "positive_log_mae": float(mean_absolute_error(
            np.log1p(positive.actual_ms), np.log1p(positive.positive_prediction_ms)
        )),
        "positive_mae_ms": float(mean_absolute_error(
            positive.actual_ms, positive.positive_prediction_ms
        )),
        "expected_mae_ms": float(mean_absolute_error(
            predictions.actual_ms, predictions.expected_prediction_ms
        )),
        "expected_r2": float(r2_score(
            predictions.actual_ms, predictions.expected_prediction_ms
        )),
        "hard_mae_ms": float(mean_absolute_error(
            predictions.actual_ms, predictions.hard_prediction_ms
        )),
        "hard_r2": float(r2_score(predictions.actual_ms, predictions.hard_prediction_ms)),
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print("\nImportance with CI above zero")
    print(bootstrap[bootstrap.ci95_low > 0].sort_values(
        ["metric", "mean"], ascending=[True, False]
    ).to_string(index=False))


if __name__ == "__main__":
    main()
