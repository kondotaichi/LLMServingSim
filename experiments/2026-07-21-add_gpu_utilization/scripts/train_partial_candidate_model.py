#!/usr/bin/env python3
"""Fit an incremental candidate-TTFT residual model from available labels."""

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
OUTPUT = ROOT / "analysis" / "partial_candidate_model.json"
PREDICTIONS = ROOT / "analysis" / "partial_candidate_model_predictions.csv"

FEATURES = [
    "predicted_total_ttft_ms",
    "predicted_scheduler_raw_ms",
    "predicted_scheduler_ms",
    "predicted_route_probability",
    "candidate_waiting_reqs",
    "candidate_running_reqs",
    "candidate_available_kv_bytes",
    "candidate_projected_active_kv_bytes",
    "candidate_capacity_pressure",
    "candidate_slot_pressure",
    "candidate_reserved_slots",
    "candidate_reserved_prefill_tokens",
    "request_migration_ms",
    "kv_migration_ms",
    "downlink_ms",
]


def fit_ridge(features, target, alpha=10.0):
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-12] = 1.0
    normalized = (features - mean) / scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    return mean, scale, coefficients


def predict(features, mean, scale, coefficients):
    normalized = (features - mean) / scale
    return coefficients[0] + normalized @ coefficients[1:]


def main():
    data = pd.read_csv(INPUT)
    features = data[FEATURES].astype(float).to_numpy()
    # Predict the residual so the existing formula remains the stable baseline.
    target = data.actual_ttft_ms.to_numpy() - data.predicted_total_ttft_ms.to_numpy()
    mean, scale, coefficients = fit_ridge(features, target)
    data["residual_prediction_ms"] = predict(features, mean, scale, coefficients)
    data["corrected_ttft_prediction_ms"] = (
        data.predicted_total_ttft_ms + data.residual_prediction_ms
    )
    data.to_csv(PREDICTIONS, index=False)

    comparable = data.groupby("request_id").filter(lambda group: len(group) >= 2)
    pair_count = sum(
        len(group) * (len(group) - 1) // 2
        for _, group in comparable.groupby("request_id")
    )
    artifact = {
        "model_type": "ridge_residual_provisional",
        "alpha": 10.0,
        "training_rows": len(data),
        "training_requests": int(data.request_id.nunique()),
        "requests_with_multiple_labels": int(comparable.request_id.nunique()),
        "within_request_pairs": int(pair_count),
        "suitable_for_policy_deployment": False,
        "features": FEATURES,
        "feature_mean": dict(zip(FEATURES, mean.tolist())),
        "feature_scale": dict(zip(FEATURES, scale.tolist())),
        "intercept": float(coefficients[0]),
        "coefficients": dict(zip(FEATURES, coefficients[1:].tolist())),
        "training_mae_ms": float(
            np.mean(np.abs(data.actual_ttft_ms - data.corrected_ttft_prediction_ms))
        ),
        "note": (
            "This is an incremental diagnostic model. Do not deploy it until "
            "multiple candidates are labeled for several independent requests."
        ),
    }
    OUTPUT.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: artifact[key] for key in [
        "training_rows", "training_requests", "requests_with_multiple_labels",
        "within_request_pairs", "training_mae_ms",
        "suitable_for_policy_deployment",
    ]}, indent=2))


if __name__ == "__main__":
    main()
