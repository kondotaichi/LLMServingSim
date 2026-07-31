#!/usr/bin/env python3
"""End-to-end scenario-held-out evaluation of the combined regression-
accuracy improvements found so far:

  1. route_tail: learning_rate=0.15, n_estimators=70 (was 0.05/100)
  2. route_tail/route_event/scheduler: + capacity_overload, slot_overload
     hinge features (derived from existing router_initial_capacity_pressure/
     slot_pressure, shared NUMERIC_FEATURES columns)
  3. compute: + router_initial_running_reqs, router_initial_waiting_reqs

Each was validated independently (route_tail_hyperparameter_tuning.md,
tune_feature_additions.py); this script runs the full pipeline once with
all three combined, under the same protocol as fit_ttft_formula.py's
cross_validate(), to get one honest end-to-end TTFT MAE/R2 rather than
naively summing the isolated deltas (component MAEs are not strictly
additive). No production artifact is touched -- this is a read-only
what-if evaluation.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_ttft_formula as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis" / "ttft_formula"

ROUTE_TAIL_HYPERPARAMS = dict(
    loss="absolute_error", n_estimators=70, learning_rate=0.15,
    max_depth=2, min_samples_leaf=20,
)
EXTRA_NUMERIC = ["capacity_overload", "slot_overload"]
NUMERIC_FEATURES = base.NUMERIC_FEATURES + EXTRA_NUMERIC
FEATURES = NUMERIC_FEATURES + base.CATEGORICAL_FEATURES
COMPUTE_FEATURES = base.COMPUTE_FEATURES + [
    "router_initial_running_reqs", "router_initial_waiting_reqs",
]


def prepare_dataset():
    dataset = base.prepare_dataset()
    dataset["capacity_overload"] = np.maximum(
        0.0, dataset.router_initial_capacity_pressure - 1.0
    )
    dataset["slot_overload"] = np.maximum(
        0.0, dataset.router_initial_slot_pressure - 1.0
    )
    return dataset


def make_preprocessor():
    return ColumnTransformer([
        ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         base.CATEGORICAL_FEATURES),
    ])


def fit_components(train):
    preprocessor = make_preprocessor()
    transformed = preprocessor.fit_transform(train[FEATURES])
    event_model = LogisticRegression(
        C=0.1, class_weight="balanced", max_iter=2000, random_state=base.RANDOM_STATE
    ).fit(transformed, train.route_positive)

    positive = train.route_positive.to_numpy(dtype=bool)
    positive_log_route = np.log1p(train.loc[positive, "router_queue_ms"])
    tail_model = GradientBoostingRegressor(
        random_state=base.RANDOM_STATE, **ROUTE_TAIL_HYPERPARAMS,
    ).fit(transformed[positive], positive_log_route)

    log_scheduler_target = np.log1p(train.scheduler_queue_ms)
    scheduler_model = Ridge(alpha=1000.0).fit(transformed, log_scheduler_target)

    compute_model = LinearRegression().fit(train[COMPUTE_FEATURES], train.compute_prefill_ms)

    return {
        "preprocessor": preprocessor,
        "route_event": event_model,
        "route_tail": tail_model,
        "route_log_bounds": (float(positive_log_route.min()), float(positive_log_route.max())),
        "scheduler": scheduler_model,
        "scheduler_log_bounds": (float(log_scheduler_target.min()), float(log_scheduler_target.max())),
        "compute": compute_model,
    }


def predict_components(models, frame):
    transformed = models["preprocessor"].transform(frame[FEATURES])
    route_probability = models["route_event"].predict_proba(transformed)[:, 1]
    route_log = np.clip(models["route_tail"].predict(transformed), *models["route_log_bounds"])
    route_positive_ms = np.expm1(route_log)
    route_ms = route_probability * route_positive_ms
    scheduler_log = np.clip(models["scheduler"].predict(transformed), *models["scheduler_log_bounds"])
    scheduler_ms = np.expm1(scheduler_log)
    compute_ms = np.maximum(0, models["compute"].predict(frame[COMPUTE_FEATURES]))
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


def main():
    dataset = prepare_dataset()
    print(f"n={len(dataset)}, n_scenarios={dataset.scenario_id.nunique()}")
    start = time.time()
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
    predictions = pd.concat(outputs, ignore_index=True)
    print(f"CV done in {time.time() - start:.1f}s")

    summary = {
        "n": len(predictions),
        "n_scenarios": int(predictions.test_scenario.nunique()),
        "route_mae_ms": mean_absolute_error(predictions.actual_route_ms, predictions.predicted_route_ms),
        "scheduler_mae_ms": mean_absolute_error(predictions.actual_scheduler_ms, predictions.predicted_scheduler_ms),
        "compute_mae_ms": mean_absolute_error(predictions.actual_compute_ms, predictions.predicted_compute_ms),
        "ttft_mae_ms": mean_absolute_error(predictions.actual_ttft_ms, predictions.predicted_ttft_ms),
        "ttft_r2": r2_score(predictions.actual_ttft_ms, predictions.predicted_ttft_ms),
    }

    baseline = {
        "route_mae_ms": 630.1895712999403,
        "scheduler_mae_ms": 48.40633448385,
        "compute_mae_ms": 59.67971875806672,
        "ttft_mae_ms": 714.6412465859496,
        "ttft_r2": 0.46634355363553925,
    }

    print()
    print(f"{'metric':<18}{'current prod':>15}{'combined new':>15}{'delta':>12}")
    for key in ["route_mae_ms", "scheduler_mae_ms", "compute_mae_ms", "ttft_mae_ms", "ttft_r2"]:
        b, n = baseline[key], summary[key]
        print(f"{key:<18}{b:>15.4f}{n:>15.4f}{n - b:>12.4f}")

    predictions.to_csv(ANALYSIS / "combined_improvements_oof_predictions.csv", index=False)
    pd.Series(summary).to_csv(ANALYSIS / "combined_improvements_summary.csv")


if __name__ == "__main__":
    main()
