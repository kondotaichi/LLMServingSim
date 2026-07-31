#!/usr/bin/env python3
"""Idea B (feature additions) from the regression-accuracy improvement
discussion: test two concrete, currently-unused-but-available feature
additions under the same scenario-held-out CV protocol used everywhere else
in this experiment.

1. compute_ms currently regresses on only [input_tokens,
   home_cached_prefix_tokens] (see fit_ttft_formula.py COMPUTE_FEATURES).
   It never sees candidate/home batch congestion at all, even though
   router_initial_running_reqs/waiting_reqs are already known at decision
   time and already feed the route/scheduler models. Test whether adding
   them to the compute linear regression reduces compute_mae_ms.

2. route_tail is a depth-2 GradientBoostingRegressor (max 3 splits/tree). A
   hinge feature (capacity_overload = max(0, capacity_pressure - 1), from
   POST_ROUTING_ANALYSIS_PLAN.md section 6.1) pre-computes a threshold that
   a depth-2 tree would otherwise need an extra split to approximate. Test
   whether adding it (plus a couple of the plan's other suggested hinges/
   interactions) improves route_mae_ms at the already-tuned
   learning_rate=0.15/n_estimators=70 hyperparameters.

No production artifact is touched.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_ttft_formula as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis" / "ttft_formula"

TUNED_ROUTE_TAIL = dict(
    loss="absolute_error", n_estimators=70, learning_rate=0.15,
    max_depth=2, min_samples_leaf=20,
)


def add_derived_features(dataset):
    dataset = dataset.copy()
    dataset["capacity_overload"] = np.maximum(
        0.0, dataset.router_initial_capacity_pressure - 1.0
    )
    dataset["slot_overload"] = np.maximum(
        0.0, dataset.router_initial_slot_pressure - 1.0
    )
    dataset["home_load_x_rate"] = (
        dataset.home_workload_share * dataset.request_rate_rps
    )
    dataset["capacity_pressure_x_rate"] = (
        dataset.router_initial_capacity_pressure * dataset.request_rate_rps
    )
    return dataset


# ---------------------------------------------------------------------
# Part A: compute_ms feature additions
# ---------------------------------------------------------------------

COMPUTE_VARIANTS = {
    "baseline (input_tokens, home_cached_prefix_tokens)":
        ["input_tokens", "home_cached_prefix_tokens"],
    "+ router_initial_running_reqs":
        ["input_tokens", "home_cached_prefix_tokens", "router_initial_running_reqs"],
    "+ router_initial_waiting_reqs":
        ["input_tokens", "home_cached_prefix_tokens", "router_initial_waiting_reqs"],
    "+ both running & waiting":
        ["input_tokens", "home_cached_prefix_tokens",
         "router_initial_running_reqs", "router_initial_waiting_reqs"],
}


def run_compute_part(dataset):
    print("=== Part A: compute_ms feature additions ===")
    rows = []
    for label, features in COMPUTE_VARIANTS.items():
        preds, actuals = [], []
        for scenario in sorted(dataset.scenario_id.unique()):
            train = dataset[dataset.scenario_id != scenario]
            test = dataset[dataset.scenario_id == scenario]
            model = LinearRegression().fit(train[features], train.compute_prefill_ms)
            pred = np.maximum(0, model.predict(test[features]))
            preds.append(pred)
            actuals.append(test.compute_prefill_ms.to_numpy())
        pred = np.concatenate(preds)
        actual = np.concatenate(actuals)
        mae = mean_absolute_error(actual, pred)
        r2 = r2_score(actual, pred)
        print(f"[{label}] compute_mae={mae:.2f}ms compute_r2={r2:.4f}")
        rows.append(dict(variant=label, compute_mae_ms=mae, compute_r2=r2))
    result = pd.DataFrame(rows)
    result.to_csv(ANALYSIS / "compute_feature_addition_sweep.csv", index=False)
    print()
    return result


# ---------------------------------------------------------------------
# Part B: route_tail hinge/interaction feature additions
# ---------------------------------------------------------------------

ROUTE_FEATURE_VARIANTS = {
    "baseline (NUMERIC_FEATURES, tuned hyperparams)": [],
    "+ capacity_overload, slot_overload": ["capacity_overload", "slot_overload"],
    "+ home_load_x_rate, capacity_pressure_x_rate":
        ["home_load_x_rate", "capacity_pressure_x_rate"],
    "+ all four": ["capacity_overload", "slot_overload",
                    "home_load_x_rate", "capacity_pressure_x_rate"],
}


def make_preprocessor_for(extra_numeric):
    numeric = base.NUMERIC_FEATURES + extra_numeric
    return numeric, ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         base.CATEGORICAL_FEATURES),
    ])


def run_route_part(dataset):
    print("=== Part B: route_tail hinge/interaction feature additions ===")
    dataset = add_derived_features(dataset)
    rows = []
    for label, extra in ROUTE_FEATURE_VARIANTS.items():
        numeric_features, _ = make_preprocessor_for(extra)
        feature_cols = numeric_features + base.CATEGORICAL_FEATURES
        pred_route_ms, actual_route_ms = [], []
        start = time.time()
        for scenario in sorted(dataset.scenario_id.unique()):
            train = dataset[dataset.scenario_id != scenario]
            test = dataset[dataset.scenario_id == scenario]

            _, preprocessor = make_preprocessor_for(extra)
            train_x = preprocessor.fit_transform(train[feature_cols])
            test_x = preprocessor.transform(test[feature_cols])

            event_model = LogisticRegression(
                C=0.1, class_weight="balanced", max_iter=2000,
                random_state=base.RANDOM_STATE,
            ).fit(train_x, train.route_positive)
            route_probability = event_model.predict_proba(test_x)[:, 1]

            positive = train.route_positive.to_numpy(dtype=bool)
            positive_log_route = np.log1p(train.loc[positive, "router_queue_ms"])
            tail_model = GradientBoostingRegressor(
                random_state=base.RANDOM_STATE, **TUNED_ROUTE_TAIL,
            ).fit(train_x[positive], positive_log_route)

            log_bounds = (float(positive_log_route.min()), float(positive_log_route.max()))
            route_log = np.clip(tail_model.predict(test_x), *log_bounds)
            route_positive_ms = np.expm1(route_log)
            pred_route_ms.append(route_probability * route_positive_ms)
            actual_route_ms.append(test.router_queue_ms.to_numpy())

        pred = np.concatenate(pred_route_ms)
        actual = np.concatenate(actual_route_ms)
        mae = mean_absolute_error(actual, pred)
        r2 = r2_score(actual, pred)
        elapsed = time.time() - start
        print(f"[{label}] route_mae={mae:.2f}ms route_r2={r2:.4f} ({elapsed:.1f}s)")
        rows.append(dict(variant=label, route_mae_ms=mae, route_r2=r2))
    result = pd.DataFrame(rows)
    result.to_csv(ANALYSIS / "route_tail_feature_addition_sweep.csv", index=False)
    print()
    return result


def main():
    dataset = base.prepare_dataset()
    print(f"n={len(dataset)}, n_scenarios={dataset.scenario_id.nunique()}")
    print()
    run_compute_part(dataset)
    run_route_part(dataset)


if __name__ == "__main__":
    main()
