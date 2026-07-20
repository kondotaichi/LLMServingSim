#!/usr/bin/env python3
"""Predict TTFT components with the exploratory 12-run model bundle."""

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


DEFAULT_MODEL = (
    Path(__file__).resolve().parents[1]
    / "models/prototype_12runs/component_models.joblib"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Canonical feature CSV")
    parser.add_argument("--output", required=True, help="Prediction CSV")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    return parser.parse_args()


def require_columns(frame, columns):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {missing}")


def predict_kv_transfer(frame, bundle):
    moved = frame["kv_moved"].to_numpy(dtype=float)
    migration_bytes = frame["kv_migration_bytes"].to_numpy(dtype=float)
    bandwidth = frame["kv_bandwidth_gbps"].replace(0, np.nan).to_numpy(dtype=float)
    distance_latency_ns = moved * bundle["kv_fixed_distance_latency_ns"]
    serialization_ns = np.where(
        moved > 0, 8.0 * migration_bytes / bandwidth, 0.0
    )
    staging_ns = np.where(
        moved > 0,
        2.0 * (
            migration_bytes / bundle["kv_staging_bandwidth_gbytes_per_s"]
            + bundle["kv_staging_latency_ns"]
        ),
        0.0,
    )
    return (distance_latency_ns + serialization_ns + staging_ns) / 1e6


def main():
    args = parse_args()
    bundle = joblib.load(args.model)
    frame = pd.read_csv(args.input)
    required = list(dict.fromkeys(
        bundle["router_columns"]
        + bundle["scheduler_columns"]
        + bundle["compute_columns"]
        + ["kv_moved", "kv_migration_bytes", "kv_bandwidth_gbps"]
    ))
    require_columns(frame, required)

    router_probability = bundle["router_event"].predict_proba(
        frame[bundle["router_columns"]]
    )[:, 1]
    router_positive = np.expm1(bundle["router_positive"].predict(
        frame[bundle["router_columns"]]
    ))
    router_positive = np.clip(
        router_positive, 0, bundle["router_positive_cap_ms"]
    )
    router = router_probability * router_positive
    scheduler = np.clip(bundle["scheduler"].predict(
        frame[bundle["scheduler_columns"]]
    ), 0, None)
    compute = np.clip(bundle["compute"].predict(
        frame[bundle["compute_columns"]]
    ), 0, None)
    kv_transfer = predict_kv_transfer(frame, bundle)

    output = frame.copy()
    output["predicted_router_queue_probability"] = router_probability
    output["predicted_router_positive_ms"] = router_positive
    output["predicted_router_queue_ms"] = router
    output["predicted_scheduler_queue_ms"] = scheduler
    output["predicted_kv_transfer_ms"] = kv_transfer
    output["predicted_compute_ms"] = compute
    output["predicted_component_sum_ms"] = (
        router + scheduler + kv_transfer + compute
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False)
    print(f"Wrote {len(output)} predictions to {output_path}")


if __name__ == "__main__":
    main()
