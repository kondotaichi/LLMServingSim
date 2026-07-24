#!/usr/bin/env python3
"""Apply the exported send-time TTFT formula to a feature CSV."""

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models/ttft_formula/ttft_formula.joblib"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv")
    parser.add_argument("output_csv")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    args = parser.parse_args()
    model = joblib.load(args.model)
    frame = pd.read_csv(args.input_csv)
    transformed = model["preprocessor"].transform(frame[model["features"]])
    probability = model["route_event"].predict_proba(transformed)[:, 1]
    log_route = np.clip(
        model["route_tail"].predict(transformed), *model["route_log_bounds"]
    )
    positive_route = np.expm1(log_route)
    route = probability * positive_route
    # See MODEL_ITERATION_HISTORY.md item 12/13 (experiments/
    # 2026-07-21-add_gpu_utilization/): the scheduler model now predicts
    # log1p(scheduler_ms); recover ms via clip-then-expm1 instead of the
    # old max(0, raw_ms), which used to collapse many candidates to
    # identical predictions whenever the raw ms output went negative.
    if "scheduler_log_bounds" in model:
        scheduler_log = np.clip(
            model["scheduler"].predict(transformed), *model["scheduler_log_bounds"]
        )
        scheduler = np.expm1(scheduler_log)
    else:
        scheduler = np.maximum(0, model["scheduler"].predict(transformed))
    compute = np.maximum(
        0, model["compute"].predict(frame[model["compute_features"]])
    )
    communication = np.full(
        len(frame), model["communication_point_prediction_ms"]
    )
    output = frame.copy()
    output["predicted_route_probability"] = probability
    output["predicted_route_positive_ms"] = positive_route
    output["predicted_route_ms"] = route
    output["predicted_scheduler_ms"] = scheduler
    output["predicted_compute_ms"] = compute
    output["predicted_communication_ms"] = communication
    output["predicted_ttft_ms"] = route + scheduler + compute + communication
    output.to_csv(args.output_csv, index=False)


if __name__ == "__main__":
    main()
