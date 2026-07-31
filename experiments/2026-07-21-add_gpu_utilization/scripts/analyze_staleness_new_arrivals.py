#!/usr/bin/env python3
"""Item 2 of the improvement roadmap: quantify whether the request-51-style
prediction failure (a candidate that looked idle at routing-decision time but
was congested by the time the redirected request actually completed) is
caused by genuine temporal staleness -- new requests arriving at the
candidate GPU strictly after the routing decision, which no decision-time
snapshot feature could possibly have seen -- rather than by the formula
under-weighting an already-visible feature.

For every one of the 161 counterfactual/baseline candidates, this reads the
per-candidate simulation trajectory (counterfactual/results/request{r}_target{c}/
requests.csv for counterfactual rows, results/<policy>/requests.csv for the 18
baseline-selected rows), and counts how many *other* requests were assigned to
that same candidate GPU (`gpu_id`) with `gpu_arrival_time_ns` inside the
window [router_decision_time_ns, first_token_ready_time_ns] of the candidate
request itself. That window is exactly the span the routing decision's
snapshot needed to predict but a point-in-time snapshot cannot.
"""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "analysis" / "counterfactual_candidate_actuals_new_formula.csv"
BASELINE_REQUESTS = ROOT / "results" / "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE" / "requests.csv"
ANALYSIS = ROOT / "analysis"

_requests_cache = {}


def load_requests(source, request_id, candidate_id):
    if source == "baseline":
        path = BASELINE_REQUESTS
    else:
        path = ROOT / "counterfactual" / "results" / f"request{request_id}_target{candidate_id}" / "requests.csv"
    if path not in _requests_cache:
        if not path.exists():
            return None
        _requests_cache[path] = pd.read_csv(path)
    return _requests_cache[path]


def main():
    meta = pd.read_csv(META)
    complete = meta[meta.counterfactual_complete].copy()

    rows = []
    missing = []
    for row in complete.itertuples(index=False):
        trajectory = load_requests(row.source, row.request_id, row.candidate_instance_id)
        if trajectory is None:
            missing.append((row.request_id, row.candidate_instance_id, row.source))
            continue
        own = trajectory[trajectory["request id"] == row.request_id]
        if own.empty:
            missing.append((row.request_id, row.candidate_instance_id, row.source))
            continue
        own = own.iloc[0]
        decision = own["router_decision_time_ns"]
        window_end = own["first_token_ready_time_ns"]
        others = trajectory[
            (trajectory.gpu_id == row.candidate_instance_id)
            & (trajectory["request id"] != row.request_id)
            & (trajectory.gpu_arrival_time_ns >= decision)
            & (trajectory.gpu_arrival_time_ns <= window_end)
        ]
        rows.append(dict(
            request_id=row.request_id,
            candidate_instance_id=row.candidate_instance_id,
            source=row.source,
            window_ms=(window_end - decision) / 1e6,
            new_arrivals_during_window=len(others),
            candidate_waiting_reqs=row.candidate_waiting_reqs,
            candidate_running_reqs=row.candidate_running_reqs,
            predicted_total_ttft_ms=row.predicted_total_ttft_ms,
            actual_ttft_ms=row.actual_ttft_ms,
            abs_err_ms=abs(row.actual_ttft_ms - row.predicted_total_ttft_ms),
            actual_ttft_rank=row.actual_ttft_rank,
        ))

    if missing:
        print(f"WARNING: {len(missing)} rows could not be resolved to a trajectory file: {missing}")

    detail = pd.DataFrame(rows)
    detail.to_csv(ANALYSIS / "staleness_new_arrivals_by_candidate.csv", index=False)

    print(f"n={len(detail)} candidates analyzed")
    print()
    print("=== Correlation with prediction error ===")
    for col in ["new_arrivals_during_window", "candidate_waiting_reqs", "candidate_running_reqs"]:
        print(f"{col:30s} corr(abs_err_ms) = {detail[col].corr(detail.abs_err_ms):.3f}")

    print()
    print("=== abs_err_ms by new-arrival count ===")
    summary = detail.groupby("new_arrivals_during_window").agg(
        n=("request_id", "size"),
        mean_abs_err_ms=("abs_err_ms", "mean"),
        median_abs_err_ms=("abs_err_ms", "median"),
        mean_actual_ttft_rank=("actual_ttft_rank", "mean"),
    )
    print(summary.to_string())

    print()
    print("=== Idle-looking candidates only (candidate_waiting_reqs == 0) ===")
    idle = detail[detail.candidate_waiting_reqs == 0]
    idle_summary = idle.groupby(idle.new_arrivals_during_window > 0).agg(
        n=("request_id", "size"),
        mean_abs_err_ms=("abs_err_ms", "mean"),
        median_abs_err_ms=("abs_err_ms", "median"),
    )
    idle_summary.index.name = "has_new_arrival_during_window"
    print(idle_summary.to_string())

    summary.to_csv(ANALYSIS / "staleness_new_arrivals_summary.csv")
    idle_summary.to_csv(ANALYSIS / "staleness_idle_candidates_summary.csv")


if __name__ == "__main__":
    main()
