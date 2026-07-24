#!/usr/bin/env python3
"""Analyze candidate-level formula predictions and pressure/model rankings."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
POLICY = "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"
INPUT = ROOT / "results" / POLICY / "routing_candidates.csv"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"


def main():
    if not INPUT.exists():
        raise FileNotFoundError(
            f"Missing {INPUT}; rerun the learned Multi-candidate policy."
        )
    frame = pd.read_csv(INPUT)
    if frame.empty:
        raise ValueError(f"No candidate diagnostics in {INPUT}")
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    frame["scheduler_clipped_ms"] = (
        frame.predicted_scheduler_ms - frame.predicted_scheduler_raw_ms
    )
    frame["rank_delta_model_minus_pressure"] = (
        frame.model_ttft_rank - frame.capacity_pressure_rank
    )
    frame.to_csv(ANALYSIS / "redirect_candidate_predictions.csv", index=False)

    request_summary = frame.groupby("request_id", as_index=False).agg(
        candidates=("candidate_instance_id", "count"),
        predicted_min_ms=("predicted_total_ttft_ms", "min"),
        predicted_max_ms=("predicted_total_ttft_ms", "max"),
        scheduler_raw_min_ms=("predicted_scheduler_raw_ms", "min"),
        scheduler_raw_max_ms=("predicted_scheduler_raw_ms", "max"),
        zero_scheduler_candidates=("predicted_scheduler_ms", lambda values: int(values.eq(0).sum())),
        pressure_top1_gpu=("candidate_instance_id", lambda values: int(
            frame.loc[values.index].sort_values("capacity_pressure_rank").iloc[0].candidate_instance_id
        )),
        model_top1_gpu=("candidate_instance_id", lambda values: int(
            frame.loc[values.index].sort_values("model_ttft_rank").iloc[0].candidate_instance_id
        )),
    )
    request_summary["predicted_range_ms"] = (
        request_summary.predicted_max_ms - request_summary.predicted_min_ms
    )
    request_summary["top1_agrees"] = (
        request_summary.pressure_top1_gpu == request_summary.model_top1_gpu
    )
    request_summary.to_csv(
        ANALYSIS / "redirect_candidate_request_summary.csv", index=False
    )

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    axes[0].scatter(frame.candidate_capacity_pressure,
                    frame.predicted_scheduler_raw_ms,
                    c=frame.model_ttft_rank, cmap="viridis", alpha=0.75)
    axes[0].axhline(0, color="#c74b3a", linestyle="--", linewidth=1.5)
    axes[0].set_xlabel("candidate capacity pressure")
    axes[0].set_ylabel("Scheduler prediction before clipping (ms)")
    axes[0].set_title("Why Scheduler predictions become zero")

    axes[1].scatter(frame.capacity_pressure_rank, frame.model_ttft_rank,
                    alpha=0.55, color="#31866f")
    limit = max(frame.capacity_pressure_rank.max(), frame.model_ttft_rank.max())
    axes[1].plot([1, limit], [1, limit], color="#77736d", linestyle="--")
    axes[1].set_xlabel("capacity-pressure rank")
    axes[1].set_ylabel("model TTFT rank")
    axes[1].set_title("Pressure rank versus model rank")

    ordered = frame.sort_values(["request_id", "candidate_instance_id"])
    centered = ordered.predicted_total_ttft_ms - ordered.groupby(
        "request_id"
    ).predicted_total_ttft_ms.transform("min")
    axes[2].hist(centered, bins=30, color="#865bd6", alpha=0.8)
    axes[2].set_xlabel("predicted candidate regret from model minimum (ms)")
    axes[2].set_ylabel("candidate rows")
    axes[2].set_title("Candidate prediction spread")
    for axis in axes:
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(FIGURES / "candidate_prediction_diagnostics.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    print(request_summary.to_string(index=False))
    print(f"Candidate rows: {len(frame)}")
    print(f"Redirect decisions: {frame.request_id.nunique()}")
    print(f"Scheduler predictions clipped to zero: {frame.predicted_scheduler_ms.eq(0).mean():.1%}")
    print(f"Pressure/model top-1 agreement: {request_summary.top1_agrees.mean():.1%}")


if __name__ == "__main__":
    main()
