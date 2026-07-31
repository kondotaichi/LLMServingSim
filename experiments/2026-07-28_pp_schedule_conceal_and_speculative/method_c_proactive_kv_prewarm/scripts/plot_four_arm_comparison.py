#!/usr/bin/env python3
"""Four-arm (naive / A only / C only / A+C) TTFT breakdown + CDF comparison
for input8000_reuse025 / input8000_reuse05, on both PP1 and PP2.

Mirrors the visual style of experiments/2026-07-22_pp2_five_workloads/
scripts/analyze_pp_comparison.py's plot_input8000_redirect_ttft_breakdown()
and plot_cdfs(), but the 4 rows/lines are arms (naive/A/C/A+C) at a fixed
topology+workload, instead of topologies at a fixed workload.

C only was never run before this script was requested -- see
results/c_only/{pp1,pp2}_input8000_reuse0{25,5}/. A+C uses each topology's
own best Stage 2 setting (PP2: threshold 0.6/top-K 3, PP1: threshold
0.8/top-K 3 -- PP1's own sweep found 0.8 better than PP2's winning 0.6,
see reports/verification.md).
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PP2_FIVE = Path(__file__).resolve().parents[3] / "2026-07-22_pp2_five_workloads"
METHOD_AB = Path(__file__).resolve().parents[2]
FIGURES = ROOT / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

SETTINGS = [
    ("pp1", "input8000_reuse025", "PP1 / 8000 / reuse 25%"),
    ("pp1", "input8000_reuse05", "PP1 / 8000 / reuse 50%"),
    ("pp2", "input8000_reuse025", "PP2 / 8000 / reuse 25%"),
    ("pp2", "input8000_reuse05", "PP2 / 8000 / reuse 50%"),
]
ARMS = ["Naive", "A only", "C only", "A+C"]
ARM_COLORS = {
    "Naive": "#4c78a8",
    "A only": "#f58518",
    "C only": "#54a24b",
    "A+C": "#b279a2",
}
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def _path(topology, arm, workload):
    if arm == "Naive":
        return PP2_FIVE / "results" / topology / workload / "requests.csv"
    if arm == "A only":
        return METHOD_AB / "results" / f"{topology}_scheduler_hide" / workload / "requests.csv"
    if arm == "C only":
        return ROOT / "results" / "c_only" / f"{topology}_{workload}" / "requests.csv"
    if arm == "A+C":
        if topology == "pp1":
            return ROOT / "results" / "pp1_proactive_prewarm" / workload / "requests.csv"
        if workload == "input8000_reuse025":
            return ROOT / "results" / "stage2_sweep" / "th0.6_k3" / "requests.csv"
        return ROOT / "results" / "stage4_sweep" / workload / "requests.csv"
    raise ValueError(arm)


def load_all():
    data = {}
    for topology, workload, _ in SETTINGS:
        for arm in ARMS:
            path = _path(topology, arm, workload)
            if path.exists():
                data[(topology, workload, arm)] = pd.read_csv(path)
    return data


def breakdown(frame, scheduler_hide):
    communication = frame.communication_latency_ns / 1e6
    transfer = frame.kv_migration_latency_ns / 1e6
    scheduler = frame.queueing_before_ttft_ns / 1e6
    prefill = frame.prefill_service_ns / 1e6
    total = frame.e2e_ttft_ns / 1e6
    other_communication = (communication - transfer).clip(lower=0)

    if scheduler_hide and "kv_migration_effective_latency_ns" in frame.columns:
        effective_transfer = frame.kv_migration_effective_latency_ns / 1e6
        exposed_transfer = (effective_transfer - scheduler).clip(lower=0)
        communication_for_router = other_communication + exposed_transfer
        transfer_display = exposed_transfer
    else:
        communication_for_router = communication
        transfer_display = transfer

    router = (total - scheduler - prefill - communication_for_router).clip(lower=0)
    return {
        "Router queue": router.mean(),
        "Scheduler queue": scheduler.mean(),
        "KV transfer": transfer_display.mean(),
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
    fig, axes = plt.subplots(2, 2, figsize=(15, 14))
    for axis, (topology, workload, title) in zip(axes.flat, SETTINGS):
        rows = []
        for cohort_name, selector in cohorts:
            for arm in ARMS:
                frame = data.get((topology, workload, arm))
                if frame is None:
                    continue
                selected = frame.loc[selector(frame)]
                if selected.empty:
                    continue
                values = breakdown(selected, scheduler_hide=(arm in ("A only", "A+C")))
                rows.append({
                    "cohort": cohort_name, "arm": arm, "n": len(selected), **values,
                })
        if not rows:
            axis.set_visible(False)
            continue
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
                              ha="center", va="center", color="white", fontsize=7.5,
                              fontweight="bold")
            left += widths
        for position, row in zip(positions, group.itertuples()):
            axis.text(row.Total + maximum * 0.015, position,
                      f"{row.Total:.0f} ms (n={row.n})",
                      va="center", fontweight="bold", fontsize=8.5)
        axis.set_yticks(positions, labels, fontsize=8)
        axis.set_ylim(positions[-1] + 0.7, -0.7)
        axis.set_xlim(0, maximum * 1.28)
        axis.set_title(title)
        axis.set_xlabel("Mean E2E TTFT components (ms)")
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    handles, labels_ = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=len(COMPONENTS), frameon=False)
    fig.suptitle("Method A / C / A+C: mean E2E TTFT breakdown by redirect outcome (input8000)",
                 fontsize=16)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    fig.savefig(FIGURES / "four_arm_ttft_breakdown.png", dpi=180, bbox_inches="tight",
                facecolor="#faf8f4")
    plt.close(fig)


def plot_cdfs(data):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), squeeze=False)
    for axis, (topology, workload, title) in zip(axes.flat, SETTINGS):
        for arm in ARMS:
            frame = data.get((topology, workload, arm))
            if frame is None:
                continue
            values = np.sort(frame.e2e_ttft_ns.to_numpy() / 1e6)
            cdf = np.arange(1, len(values) + 1) / len(values)
            axis.plot(values, cdf, linewidth=2.2, color=ARM_COLORS[arm], label=arm)
        axis.set_title(title)
        axis.set_xlabel("E2E TTFT (ms)")
        axis.set_ylabel("CDF")
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    axes[0, 0].legend(frameon=False)
    fig.suptitle("Naive / A only / C only / A+C: per-request E2E TTFT CDF (input8000)",
                 fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGURES / "four_arm_ttft_cdf.png", dpi=180, bbox_inches="tight",
                facecolor="#faf8f4")
    plt.close(fig)


def main():
    data = load_all()
    missing = [
        (topology, workload, arm)
        for topology, workload, _ in SETTINGS
        for arm in ARMS
        if (topology, workload, arm) not in data
    ]
    for m in missing:
        print(f"Missing: {m}")
    plot_breakdown(data)
    plot_cdfs(data)
    print(f"Figures written to {FIGURES}")


if __name__ == "__main__":
    main()
