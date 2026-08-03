#!/usr/bin/env python3
"""TTFT breakdown for the Hongo peak_3x five-arm comparison. Mirrors the
stacked-bar format used in
experiments/2026-07-21-add_gpu_utilization/scripts/analyze_utilization.py.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"

ARMS = [
    ("1: no_redirect", "peak_3x_seed1_1_no_redirect", 1),
    ("2: redirect_no_kv", "peak_3x_seed1_2_redirect_no_kv", 1),
    ("3: redirect_kv_nopp", "peak_3x_seed1_3_redirect_kv_nopp", 1),
    ("4: redirect_kv_pp2", "peak_3x_seed1_4_redirect_kv_pp2", 2),
    ("5: redirect_kv_pp2_c", "peak_3x_seed1_5_redirect_kv_pp2_c", 2),
]
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("PP transfer (est.)", "#e0459b"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]

# Analytical PP inter-stage transfer estimate. Mirrors the byte-size formula
# trace_generator.py's _emit_pp_pd_power uses (total_len * hidden_size * fp
# bytes per boundary), divided by the intra-PP-group network.yml bandwidth/
# latency (confirmed via a live --no-cleanup-inputs smoke run: a real
# COMM_SEND/COMM_RECV pair of 16,777,216 bytes at a batch's PP boundary).
# This is a per-request approximation using each request's own input token
# count as a stand-in for its share of the batch's boundary activation, not
# a measurement of the actual (batch-level, contention-subject) ASTRA-Sim
# transfer time -- requests.csv has no column that isolates it directly.
HIDDEN_SIZE = 4096
FP_BYTES = 2  # bfloat16
LINK_BANDWIDTH_GBPS = 16.0  # GB/s, network.yml intra-PP-group dimension
LINK_LATENCY_NS = 20000.0  # ns, per hop


def load_available():
    frames = {}
    for label, dirname, pp_size in ARMS:
        path = RESULTS / dirname / "requests.csv"
        if path.exists():
            frame = pd.read_csv(path)
            if len(frame) >= 2000:
                frames[label] = (frame, pp_size)
    return frames


def estimate_pp_transfer_ns(frame, pp_size):
    if pp_size <= 1:
        return pd.Series(0.0, index=frame.index)
    boundary_bytes = frame.input * HIDDEN_SIZE * FP_BYTES * (pp_size - 1)
    return boundary_bytes / LINK_BANDWIDTH_GBPS + LINK_LATENCY_NS * (pp_size - 1)


def breakdown(frame, pp_size):
    communication = frame.communication_latency_ns.mean() / 1e6
    transfer = frame.kv_migration_latency_ns.mean() / 1e6
    pp_transfer_ns = estimate_pp_transfer_ns(frame, pp_size)
    pp_transfer = pp_transfer_ns.mean() / 1e6
    prefill_incl_pp = frame.prefill_service_ns.mean() / 1e6
    router_queue = (
        frame.e2e_ttft_ns
        - frame.prefill_service_ns
        - frame.communication_latency_ns
        - frame.queueing_before_ttft_ns
    ).clip(lower=0).mean() / 1e6
    return {
        "Router queue": router_queue,
        "Scheduler queue": frame.queueing_before_ttft_ns.mean() / 1e6,
        "KV transfer": transfer,
        "PP transfer (est.)": pp_transfer,
        "Compute / prefill": max(0.0, prefill_incl_pp - pp_transfer),
        "RTT / other comm": max(0.0, communication - transfer),
        "Total": frame.e2e_ttft_ns.mean() / 1e6,
    }


def make_summary(frames):
    rows = []
    for label, (frame, pp_size) in frames.items():
        ttft = frame.e2e_ttft_ns / 1e6
        rows.append({
            "arm": label,
            "requests": len(frame),
            "mean_ttft_ms": ttft.mean(),
            "p50_ttft_ms": ttft.quantile(0.50),
            "p95_ttft_ms": ttft.quantile(0.95),
            "p99_ttft_ms": ttft.quantile(0.99),
            "max_ttft_ms": ttft.max(),
            "redirects": int(frame.rerouted.sum()) if "rerouted" in frame else 0,
            **breakdown(frame, pp_size),
        })
    summary = pd.DataFrame(rows)
    ANALYSIS.mkdir(exist_ok=True)
    summary.to_csv(ANALYSIS / "peak_3x_ttft_breakdown.csv", index=False)
    return summary


def plot(summary):
    labels = summary.arm.tolist()
    positions = np.arange(len(labels))
    left = np.zeros(len(labels))
    maximum = summary.Total.max()
    fig, axis = plt.subplots(figsize=(13, 4 + 0.9 * len(labels)))
    for component, color in COMPONENTS:
        widths = summary[component].to_numpy()
        axis.barh(positions, widths, left=left, height=0.55, color=color,
                  edgecolor="#faf8f4", linewidth=2, label=component)
        left += widths
    for position, row in enumerate(summary.itertuples()):
        axis.text(row.Total + maximum * 0.015, position,
                   f"{row.Total:.0f} ms", va="center", fontweight="bold")
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.2)
    axis.set_xlabel("mean E2E TTFT components (ms)")
    axis.set_title("Hongo peak_3x (2000 req, seed1): five-arm TTFT breakdown")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", ncol=3, frameon=False)
    fig.tight_layout()
    FIGURES.mkdir(exist_ok=True)
    fig.savefig(FIGURES / "peak_3x_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    frames = load_available()
    if not frames:
        raise SystemExit("No completed arms found yet.")
    summary = make_summary(frames)
    plot(summary)
    print(summary.to_string(index=False))
    print(f"\nSaved figure to {FIGURES / 'peak_3x_ttft_breakdown.png'}")


if __name__ == "__main__":
    main()
