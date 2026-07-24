#!/usr/bin/env python3
"""Fit and export an interpretable send-time TTFT component formula."""

import argparse
import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "analysis/post_routing/canonical_requests.csv"
DEFAULT_OUTPUT_DIR = ROOT / "analysis/ttft_formula"
DEFAULT_FIGURE_DIR = ROOT / "figures/ttft_formula"
DEFAULT_MODEL_DIR = ROOT / "models/ttft_formula"
RANDOM_STATE = 20260720
ROUTE_UPPER_QUANTILE = 0.90

# Mutable module-level paths so a trial run (--output-dir/--model-dir) can
# write to a scratch location instead of overwriting the production
# artifacts consumed by default by serving/core/ttft_formula.py.
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
FIGURE_DIR = DEFAULT_FIGURE_DIR
MODEL_DIR = DEFAULT_MODEL_DIR

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
CATEGORICAL_FEATURES = ["policy"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
COMPUTE_FEATURES = ["input_tokens", "home_cached_prefix_tokens"]


def prepare_dataset():
    dataset = pd.read_csv(DATASET_PATH)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["output_tokens"] = dataset.output_tokens_actual
    # Proxy for prefix bytes known on the home GPU at request send time. The
    # current CSV only retains policy-dependent realized reuse, so the analysis
    # reconstructs the common pre-route value as the per-request policy maximum.
    dataset["home_cached_prefix_tokens"] = dataset.nominal_reuse_tokens
    dataset["route_positive"] = (dataset.router_queue_ms > 1e-9).astype(int)
    dataset["communication_ms"] = (
        dataset.kv_transfer_ms + dataset.other_communication_ms
    )
    return dataset


def make_preprocessor():
    return ColumnTransformer([
        ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         CATEGORICAL_FEATURES),
    ])


def fit_components(train):
    preprocessor = make_preprocessor()
    transformed = preprocessor.fit_transform(train[FEATURES])
    event_model = LogisticRegression(
        C=0.1, class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE
    ).fit(transformed, train.route_positive)

    positive = train.route_positive.to_numpy(dtype=bool)
    positive_log_route = np.log1p(train.loc[positive, "router_queue_ms"])
    tail_model = GradientBoostingRegressor(
        loss="absolute_error",
        n_estimators=100,
        learning_rate=0.05,
        max_depth=2,
        min_samples_leaf=20,
        random_state=RANDOM_STATE,
    ).fit(transformed[positive], positive_log_route)

    # log1p/expm1 (same treatment as the route tail model below) instead of
    # fitting the ms-scale target directly and clipping negative predictions
    # to 0 at serving time. The plain-ms Ridge fit produced negative raw
    # predictions for ~48% of candidates in production traffic; clipping
    # those to a hard 0 collapses many same-request candidates to identical
    # total-TTFT predictions (compute_ms is candidate-invariant by design,
    # so scheduler_ms was the only remaining source of candidate-level
    # differentiation -- see MODEL_ITERATION_HISTORY.md item 12). log1p
    # keeps the regression monotonic and strictly positive after expm1
    # without an information-destroying clip.
    log_scheduler_target = np.log1p(train.scheduler_queue_ms)
    scheduler_model = Ridge(alpha=1000.0).fit(
        transformed, log_scheduler_target
    )
    compute_model = LinearRegression().fit(
        train[COMPUTE_FEATURES], train.compute_prefill_ms
    )
    return {
        "preprocessor": preprocessor,
        "route_event": event_model,
        "route_tail": tail_model,
        "route_log_bounds": (
            float(positive_log_route.min()), float(positive_log_route.max())
        ),
        "scheduler": scheduler_model,
        "scheduler_log_bounds": (
            float(log_scheduler_target.min()), float(log_scheduler_target.max())
        ),
        "compute": compute_model,
    }


def predict_components(models, frame):
    transformed = models["preprocessor"].transform(frame[FEATURES])
    route_probability = models["route_event"].predict_proba(transformed)[:, 1]
    route_log = np.clip(
        models["route_tail"].predict(transformed), *models["route_log_bounds"]
    )
    route_positive_ms = np.expm1(route_log)
    route_ms = route_probability * route_positive_ms
    scheduler_log = np.clip(
        models["scheduler"].predict(transformed), *models["scheduler_log_bounds"]
    )
    scheduler_ms = np.expm1(scheduler_log)
    compute_ms = np.maximum(
        0, models["compute"].predict(frame[COMPUTE_FEATURES])
    )
    # Communication is zero for 92.6% of this subset and is caused by a
    # post-send redirect/KV movement decision. The point formula uses zero;
    # its observed mean absolute omission is reported separately.
    communication_ms = np.zeros(len(frame))
    ttft_ms = route_ms + scheduler_ms + compute_ms + communication_ms
    return pd.DataFrame({
        "route_probability": route_probability,
        "route_positive_ms": route_positive_ms,
        "predicted_route_ms": route_ms,
        "predicted_scheduler_ms": scheduler_ms,
        "predicted_compute_ms": compute_ms,
        "predicted_communication_ms": communication_ms,
        "predicted_ttft_ms": ttft_ms,
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


def metrics(predictions):
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
            predictions.actual_scheduler_ms, predictions.predicted_scheduler_ms
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


def export_linear_coefficients(models):
    preprocessor = models["preprocessor"]
    transformed_names = preprocessor.get_feature_names_out()
    transformed_names = [name.split("__", 1)[1] for name in transformed_names]
    event_rows = [{
        "term": "intercept",
        "coefficient": float(models["route_event"].intercept_[0]),
    }]
    event_rows.extend(
        {"term": name, "coefficient": float(value)}
        for name, value in zip(transformed_names, models["route_event"].coef_[0])
    )
    pd.DataFrame(event_rows).to_csv(
        OUTPUT_DIR / "route_event_logistic_coefficients.csv", index=False
    )

    # NOTE: this linear combination now predicts log1p(scheduler_queue_ms),
    # not ms directly -- serving/core/ttft_formula.py applies expm1() (with
    # scheduler_ridge_meta.json's log1p_bounds clip) to recover ms. The
    # "intercept_ms" term name is kept unchanged so the CSV schema and the
    # generic _linear() reader in ttft_formula.py don't need to change.
    scheduler_rows = [{
        "term": "intercept_ms",
        "coefficient": float(models["scheduler"].intercept_),
    }]
    scheduler_rows.extend(
        {"term": name, "coefficient": float(value)}
        for name, value in zip(transformed_names, models["scheduler"].coef_)
    )
    pd.DataFrame(scheduler_rows).to_csv(
        OUTPUT_DIR / "scheduler_ridge_coefficients.csv", index=False
    )
    (OUTPUT_DIR / "scheduler_ridge_meta.json").write_text(
        json.dumps({"log1p_bounds": list(models["scheduler_log_bounds"])}, indent=2),
        encoding="utf-8",
    )

    compute_rows = [{
        "term": "intercept_ms",
        "coefficient": float(models["compute"].intercept_),
    }]
    compute_rows.extend(
        {"term": name, "coefficient": float(value)}
        for name, value in zip(COMPUTE_FEATURES, models["compute"].coef_)
    )
    pd.DataFrame(compute_rows).to_csv(
        OUTPUT_DIR / "compute_linear_coefficients.csv", index=False
    )

    scaler = preprocessor.named_transformers_["numeric"]
    pd.DataFrame({
        "feature": NUMERIC_FEATURES,
        "mean": scaler.mean_,
        "scale": scaler.scale_,
    }).to_csv(OUTPUT_DIR / "numeric_feature_scaling.csv", index=False)


def export_tail_trees(models, route_upper_residual_ms):
    model = models["route_tail"]
    names = [
        name.split("__", 1)[1]
        for name in models["preprocessor"].get_feature_names_out()
    ]
    trees = []
    for tree_index, estimator_row in enumerate(model.estimators_):
        tree = estimator_row[0].tree_
        nodes = []
        for node_id in range(tree.node_count):
            feature_index = int(tree.feature[node_id])
            is_leaf = feature_index < 0
            nodes.append({
                "node_id": node_id,
                "is_leaf": is_leaf,
                "feature": None if is_leaf else names[feature_index],
                "threshold_standardized": None if is_leaf else float(tree.threshold[node_id]),
                "left_child": None if is_leaf else int(tree.children_left[node_id]),
                "right_child": None if is_leaf else int(tree.children_right[node_id]),
                "leaf_value": float(tree.value[node_id, 0, 0]) if is_leaf else None,
                "weighted_leaf_coefficient": (
                    float(model.learning_rate * tree.value[node_id, 0, 0])
                    if is_leaf else None
                ),
            })
        trees.append({"tree_index": tree_index, "nodes": nodes})
    payload = {
        "formula": "log1p(t_route_positive_ms) = init + sum(tree leaf coefficients)",
        "initial_prediction": float(np.ravel(model.init_.constant_)[0]),
        "learning_rate": float(model.learning_rate),
        "log_prediction_bounds": list(models["route_log_bounds"]),
        "upper_prediction": {
            "method": "scenario_held_out_additive_residual",
            "quantile": ROUTE_UPPER_QUANTILE,
            "residual_ms": float(route_upper_residual_ms),
        },
        "trees": trees,
    }
    (OUTPUT_DIR / "route_positive_tree_coefficients.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def plot_predictions(predictions):
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.5))
    axes[0].scatter(predictions.actual_ttft_ms, predictions.predicted_ttft_ms,
                    s=9, alpha=0.25, color="#358a73")
    limit = max(predictions.actual_ttft_ms.max(), predictions.predicted_ttft_ms.max())
    axes[0].plot([0, limit], [0, limit], linestyle="--", color="#202020")
    axes[0].set_xlabel("Actual TTFT (ms)")
    axes[0].set_ylabel("Predicted TTFT (ms)")
    axes[0].set_title("Scenario-held-out TTFT")
    axes[1].scatter(predictions.actual_route_ms, predictions.predicted_route_ms,
                    s=9, alpha=0.25, color="#c84f3d")
    axes[1].plot([1, limit], [1, limit], linestyle="--", color="#202020")
    axes[1].set_xscale("symlog", linthresh=1)
    axes[1].set_yscale("symlog", linthresh=1)
    axes[1].set_xlabel("Actual t_route (ms)")
    axes[1].set_ylabel("Predicted t_route (ms)")
    axes[1].set_title("Scenario-held-out router component")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "ttft_formula_oof.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Where to write CSV/JSON artifacts (default: production "
             "analysis/ttft_formula, i.e. what serving/core/ttft_formula.py "
             "loads by default). Point this at a scratch directory for a "
             "trial run.",
    )
    parser.add_argument(
        "--model-dir", type=Path, default=None,
        help="Where to write the joblib model bundle (default: production "
             "models/ttft_formula).",
    )
    parser.add_argument(
        "--figure-dir", type=Path, default=None,
        help="Where to write diagnostic figures (default: production "
             "figures/ttft_formula).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    global OUTPUT_DIR, FIGURE_DIR, MODEL_DIR
    OUTPUT_DIR = args.output_dir or DEFAULT_OUTPUT_DIR
    FIGURE_DIR = args.figure_dir or DEFAULT_FIGURE_DIR
    MODEL_DIR = args.model_dir or DEFAULT_MODEL_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dataset = prepare_dataset()
    predictions = cross_validate(dataset)
    summary = metrics(predictions)
    positive = predictions.actual_route_positive.astype(bool)
    route_upper_residual_ms = max(0.0, float(np.quantile(
        predictions.loc[positive, "actual_route_ms"]
        - predictions.loc[positive, "route_positive_ms"],
        ROUTE_UPPER_QUANTILE,
    )))
    upper_prediction = (
        predictions.loc[positive, "route_positive_ms"] + route_upper_residual_ms
    )
    summary["route_upper_quantile"] = ROUTE_UPPER_QUANTILE
    summary["route_upper_residual_ms"] = route_upper_residual_ms
    summary["route_positive_upper_coverage"] = float(np.mean(
        predictions.loc[positive, "actual_route_ms"] <= upper_prediction
    ))
    final_models = fit_components(dataset)
    bundle = {
        **final_models,
        "features": FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "compute_features": COMPUTE_FEATURES,
        "communication_point_prediction_ms": 0.0,
    }
    joblib.dump(bundle, MODEL_DIR / "ttft_formula.joblib")
    predictions.to_csv(OUTPUT_DIR / "oof_predictions.csv", index=False)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    export_linear_coefficients(final_models)
    export_tail_trees(final_models, route_upper_residual_ms)
    plot_predictions(predictions)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
