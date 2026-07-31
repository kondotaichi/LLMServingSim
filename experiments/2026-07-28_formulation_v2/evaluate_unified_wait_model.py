#!/usr/bin/env python3
"""Evaluate unified route+scheduler wait-time formulations.

This keeps the original scenario-held-out split and send-time features used by
the component formula, but replaces the route hurdle and scheduler models with
one model for log1p(router_queue_ms + scheduler_queue_ms).
"""

import importlib.util
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score


ROOT = Path(__file__).resolve().parent
LEGACY_ROOT = ROOT.parent / "2026-07-16_ttft_component_regression"
LEGACY_SCRIPT = LEGACY_ROOT / "scripts/fit_ttft_formula.py"
OUTPUT_DIR = ROOT / "analysis/unified_wait"
MODEL_DIR = ROOT / "models/unified_wait"
RANDOM_STATE = 20260728


def load_legacy_module():
    spec = importlib.util.spec_from_file_location("fit_ttft_formula", LEGACY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fit_unified_models(legacy, train):
    preprocessor = legacy.make_preprocessor()
    transformed = preprocessor.fit_transform(train[legacy.FEATURES])
    target = np.log1p(train["wait_ms"])
    bounds = (float(target.min()), float(target.max()))
    return {
        "preprocessor": preprocessor,
        "log1p_bounds": bounds,
        "ridge": Ridge(alpha=1000.0).fit(transformed, target),
        "gbdt_huber": GradientBoostingRegressor(
            loss="huber",
            n_estimators=100,
            learning_rate=0.05,
            max_depth=2,
            min_samples_leaf=20,
            random_state=RANDOM_STATE,
        ).fit(transformed, target),
        "gbdt_squared": GradientBoostingRegressor(
            loss="squared_error",
            n_estimators=100,
            learning_rate=0.05,
            max_depth=2,
            min_samples_leaf=20,
            random_state=RANDOM_STATE,
        ).fit(transformed, target),
        "compute": legacy.LinearRegression().fit(
            train[legacy.COMPUTE_FEATURES], train.compute_prefill_ms
        ),
    }


def predict_unified(legacy, models, frame, model_name):
    transformed = models["preprocessor"].transform(frame[legacy.FEATURES])
    log_wait = np.clip(
        models[model_name].predict(transformed), *models["log1p_bounds"]
    )
    wait_ms = np.expm1(log_wait)
    compute_ms = np.maximum(
        0.0, models["compute"].predict(frame[legacy.COMPUTE_FEATURES])
    )
    return wait_ms, compute_ms, wait_ms + compute_ms


def summarize(frame, prefix):
    error = np.abs(frame.actual_ttft_ms - frame[f"{prefix}_ttft_ms"])
    wait_error = np.abs(frame.actual_wait_ms - frame[f"{prefix}_wait_ms"])
    return {
        "model": prefix,
        "n": len(frame),
        "n_scenarios": int(frame.test_scenario.nunique()),
        "wait_mae_ms": float(wait_error.mean()),
        "wait_abs_error_p50_ms": float(np.quantile(wait_error, 0.50)),
        "wait_abs_error_p90_ms": float(np.quantile(wait_error, 0.90)),
        "wait_abs_error_p95_ms": float(np.quantile(wait_error, 0.95)),
        "ttft_mae_ms": float(error.mean()),
        "ttft_abs_error_p50_ms": float(np.quantile(error, 0.50)),
        "ttft_abs_error_p90_ms": float(np.quantile(error, 0.90)),
        "ttft_abs_error_p95_ms": float(np.quantile(error, 0.95)),
        "ttft_abs_error_p99_ms": float(np.quantile(error, 0.99)),
        "ttft_r2": float(r2_score(frame.actual_ttft_ms, frame[f"{prefix}_ttft_ms"])),
    }


def main():
    legacy = load_legacy_module()
    dataset = legacy.prepare_dataset()
    dataset["wait_ms"] = dataset.router_queue_ms + dataset.scheduler_queue_ms

    outputs = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        unified = fit_unified_models(legacy, train)
        component = legacy.fit_components(train)
        component_prediction = legacy.predict_components(component, test)

        output = pd.DataFrame({
            "test_scenario": scenario,
            "policy": test.policy.to_numpy(),
            "request_id": test.request_id.to_numpy(),
            "actual_route_ms": test.router_queue_ms.to_numpy(),
            "actual_scheduler_ms": test.scheduler_queue_ms.to_numpy(),
            "actual_wait_ms": test.wait_ms.to_numpy(),
            "actual_compute_ms": test.compute_prefill_ms.to_numpy(),
            "actual_communication_ms": test.communication_ms.to_numpy(),
            "actual_ttft_ms": test.e2e_ttft_ms.to_numpy(),
            "component_wait_ms": (
                component_prediction.predicted_route_ms.to_numpy()
                + component_prediction.predicted_scheduler_ms.to_numpy()
            ),
            "component_ttft_ms": component_prediction.predicted_ttft_ms.to_numpy(),
        })
        for model_name in ("ridge", "gbdt_huber", "gbdt_squared"):
            wait_ms, compute_ms, ttft_ms = predict_unified(
                legacy, unified, test, model_name
            )
            output[f"unified_{model_name}_wait_ms"] = wait_ms
            output[f"unified_{model_name}_compute_ms"] = compute_ms
            output[f"unified_{model_name}_ttft_ms"] = ttft_ms
        outputs.append(output)

    predictions = pd.concat(outputs, ignore_index=True)
    summaries = pd.DataFrame([
        summarize(predictions, "component"),
        summarize(predictions, "unified_ridge"),
        summarize(predictions, "unified_gbdt_huber"),
        summarize(predictions, "unified_gbdt_squared"),
    ])

    scenario_rows = []
    for scenario, group in predictions.groupby("test_scenario", sort=True):
        for prefix in (
            "component", "unified_ridge", "unified_gbdt_huber",
            "unified_gbdt_squared",
        ):
            row = summarize(group, prefix)
            row["test_scenario"] = scenario
            scenario_rows.append(row)

    final_models = fit_unified_models(legacy, dataset)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(OUTPUT_DIR / "oof_predictions.csv", index=False)
    summaries.to_csv(OUTPUT_DIR / "model_comparison.csv", index=False)
    pd.DataFrame(scenario_rows).to_csv(
        OUTPUT_DIR / "scenario_metrics.csv", index=False
    )
    joblib.dump(final_models, MODEL_DIR / "unified_wait.joblib")
    (OUTPUT_DIR / "meta.json").write_text(json.dumps({
        "target": "log1p(router_queue_ms + scheduler_queue_ms)",
        "validation": "leave-one-scenario-out",
        "features": legacy.FEATURES,
        "candidate_models": [
            "unified_ridge", "unified_gbdt_huber", "unified_gbdt_squared"
        ],
        "gbdt_common": {
            "n_estimators": 100,
            "learning_rate": 0.05,
            "max_depth": 2,
            "min_samples_leaf": 20,
            "random_state": RANDOM_STATE,
        },
    }, indent=2), encoding="utf-8")
    print(summaries.to_string(index=False))


if __name__ == "__main__":
    main()
