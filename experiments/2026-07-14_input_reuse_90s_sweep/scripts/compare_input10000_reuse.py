#!/usr/bin/env python3
"""Compare completed input-10000 reuse levels."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = EXPERIMENT_DIR / "analysis"
FIGURE_DIR = EXPERIMENT_DIR / "figures/input10000_comparison"
CASES = [
    ("reuse 0%", "input10000_reuse00"),
    ("reuse 25%", "input10000_reuse025"),
    ("reuse 50%", "input10000_reuse05"),
]
POLICIES = ["NEAREST_KV", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV"]
COLORS = ["#2f5f9f", "#d18120", "#31866f"]


def load_summary():
    frames = []
    for reuse_label, case in CASES:
        frame = pd.read_csv(ANALYSIS_DIR / case / "summary.csv")
        frame["case"] = case
        frame["reuse"] = reuse_label
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def plot_ttft(summary):
    x = np.arange(len(CASES))
    width = 0.24
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.2))
    for offset, policy, color in zip((-width, 0, width), POLICIES, COLORS):
        subset = summary[summary.policy == policy].set_index("case")
        means = [subset.loc[case, "e2e_ttft_mean_ms"] / 1000 for _, case in CASES]
        p95s = [subset.loc[case, "e2e_ttft_p95_ms"] / 1000 for _, case in CASES]
        axes[0].bar(x + offset, means, width=width, color=color, label=policy)
        axes[1].bar(x + offset, p95s, width=width, color=color, label=policy)
    for axis, title in zip(axes, ("Mean E2E TTFT", "p95 E2E TTFT")):
        axis.set_xticks(x, [label for label, _ in CASES])
        axis.set_ylabel("seconds")
        axis.set_title(title)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(fontsize=9)
    fig.suptitle("Input 10000 / 90s: prefix-reuse comparison", fontsize=17)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "ttft_mean_p95_comparison.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_components(summary):
    labels = []
    router = []
    scheduler = []
    prefill = []
    transfer = []
    colors = []
    for reuse_label, case in CASES:
        subset = summary[summary.case == case].set_index("policy")
        for policy, color in zip(POLICIES, COLORS):
            labels.append(f"{reuse_label}\n{policy.replace('NEAREST_', '')}")
            router.append(subset.loc[policy, "router_queue_mean_ms"] / 1000)
            scheduler.append(subset.loc[policy, "scheduler_queue_mean_ms"] / 1000)
            prefill.append(subset.loc[policy, "prefill_mean_ms"] / 1000)
            transfer.append(subset.loc[policy, "kv_transfer_mean_ms"] / 1000)
            colors.append(color)
    x = np.arange(len(labels))
    fig, axis = plt.subplots(figsize=(14, 7.0))
    axis.bar(x, router, color="#c74b3a", label="Router queue")
    axis.bar(x, scheduler, bottom=router, color="#e79b37", label="Scheduler queue")
    bottom = np.array(router) + np.array(scheduler)
    axis.bar(x, transfer, bottom=bottom, color="#865bd6", label="KV transfer")
    bottom += np.array(transfer)
    axis.bar(x, prefill, bottom=bottom, color="#31866f", label="Compute / prefill")
    axis.set_xticks(x, labels)
    axis.set_ylabel("mean E2E TTFT components (seconds)")
    axis.set_title("Input 10000 / 90s: mean E2E TTFT breakdown by reuse level")
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(ncol=4)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "breakdown_comparison.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_redirects(summary):
    x = np.arange(len(CASES))
    width = 0.32
    fig, axis = plt.subplots(figsize=(10.5, 6.2))
    for offset, policy, color in zip((-width / 2, width / 2), POLICIES[1:], COLORS[1:]):
        subset = summary[summary.policy == policy].set_index("case")
        values = [subset.loc[case, "redirects"] for _, case in CASES]
        bars = axis.bar(x + offset, values, width=width, color=color, label=policy)
        axis.bar_label(bars, padding=3, fontweight="bold")
    axis.set_xticks(x, [label for label, _ in CASES])
    axis.set_ylabel("redirected requests")
    axis.set_title("Input 10000 / 90s: redirect count")
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "redirect_count_comparison.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    summary = load_summary()
    summary.to_csv(ANALYSIS_DIR / "input10000_reuse_comparison.csv", index=False)
    plot_ttft(summary)
    plot_components(summary)
    plot_redirects(summary)


if __name__ == "__main__":
    main()
