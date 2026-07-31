#!/usr/bin/env python3
"""Naive vs C-only TTFT breakdown + CDF on the genuine session-continuity
workload (sharegpt_300_deep_cell_apn.jsonl, PP1, 300 requests).
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

ARMS = ["Naive", "C only"]
ARM_COLORS = {"Naive": "#4c78a8", "C only": "#54a24b"}
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def _path(arm):
    name = "naive_deep" if arm == "Naive" else "c_only_deep"
    return ROOT / "results" / name / "requests.csv"


def load_all():
    return {arm: pd.read_csv(_path(arm)) for arm in ARMS}


def breakdown(frame):
    communication = frame.communication_latency_ns / 1e6
    transfer = frame.kv_migration_latency_ns / 1e6
    scheduler = frame.queueing_before_ttft_ns / 1e6
    prefill = frame.prefill_service_ns / 1e6
    total = frame.e2e_ttft_ns / 1e6
    other_communication = (communication - transfer).clip(lower=0)
    router = (total - scheduler - prefill - communication).clip(lower=0)
    return {
        "Router queue": router.mean(),
        "Scheduler queue": scheduler.mean(),
        "KV transfer": transfer.mean(),
        "Compute / prefill": prefill.mean(),
        "RTT / other comm": other_communication.mean(),
        "Total": total.mean(),
    }


def plot_breakdown(data):
    cohorts = [
        ("All requests", lambda f: f.index == f.index),
        ("Redirected only", lambda f: f.rerouted.astype(bool)),
        ("Not redirected", lambda f: ~f.rerouted.astype(bool)),
    ]
    fig, axis = plt.subplots(1, 1, figsize=(11, 7))
    rows = []
    for cohort_name, selector in cohorts:
        for arm in ARMS:
            frame = data[arm]
            selected = frame.loc[selector(frame)]
            if selected.empty:
                continue
            values = breakdown(selected)
            rows.append({"cohort": cohort_name, "arm": arm, "n": len(selected), **values})
    group = pd.DataFrame(rows)
    labels = [f"{r.cohort}\n{r.arm}" for r in group.itertuples()]
    positions = np.arange(len(group)) * 1.15
    left = np.zeros(len(group))
    maximum = group.Total.max()
    for component, color in COMPONENTS:
        widths = group[component].to_numpy()
        axis.barh(positions, widths, left=left, height=0.68, color=color,
                  edgecolor="#faf8f4", linewidth=2, label=component)
        for position, start, width in zip(positions, left, widths):
            if width >= maximum * 0.06 and width >= 8:
                axis.text(start + width / 2, position, f"{width:.0f}",
                          ha="center", va="center", color="white", fontsize=8,
                          fontweight="bold")
        left += widths
    for position, row in zip(positions, group.itertuples()):
        axis.text(row.Total + maximum * 0.015, position,
                  f"{row.Total:.0f} ms (n={row.n})",
                  va="center", fontweight="bold", fontsize=9)
    axis.set_yticks(positions, labels, fontsize=9)
    axis.set_ylim(positions[-1] + 0.7, -0.7)
    axis.set_xlim(0, maximum * 1.28)
    axis.set_title("Naive vs C-only (Method C): mean E2E TTFT breakdown\n"
                    "session-continuity workload (real multi-turn ShareGPT, PP1, n=300)")
    axis.set_xlabel("Mean E2E TTFT components (ms)")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    handles, labels_ = axis.get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=len(COMPONENTS), frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    fig.savefig(FIGURES / "naive_vs_c_ttft_breakdown.png", dpi=180, bbox_inches="tight",
                facecolor="#faf8f4")
    plt.close(fig)


def plot_cdf(data):
    fig, axis = plt.subplots(1, 1, figsize=(8, 6))
    for arm in ARMS:
        frame = data[arm]
        values = np.sort(frame.e2e_ttft_ns.to_numpy() / 1e6)
        cdf = np.arange(1, len(values) + 1) / len(values)
        axis.plot(values, cdf, linewidth=2.2, color=ARM_COLORS[arm], label=arm)
    axis.set_title("Naive vs C-only: per-request E2E TTFT CDF\n"
                    "session-continuity workload (real multi-turn ShareGPT, PP1, n=300)")
    axis.set_xlabel("E2E TTFT (ms)")
    axis.set_ylabel("CDF")
    axis.grid(color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIGURES / "naive_vs_c_ttft_cdf.png", dpi=180, bbox_inches="tight",
                facecolor="#faf8f4")
    plt.close(fig)


def main():
    data = load_all()
    plot_breakdown(data)
    plot_cdf(data)
    print(f"Figures written to {FIGURES}")


if __name__ == "__main__":
    main()
