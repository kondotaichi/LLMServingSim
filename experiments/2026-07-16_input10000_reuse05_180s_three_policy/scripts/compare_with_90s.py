#!/usr/bin/env python3
"""Compare input-10000/reuse-50% results between 90s and 180s."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EXPERIMENT_DIR.parents[1]
OLD_SUMMARY = (
    REPO_ROOT / "experiments/2026-07-14_input_reuse_90s_sweep/"
    "analysis/input10000_reuse05/summary.json"
)
POLICIES = ["NEAREST_KV", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV"]


def load_summary(path):
    with path.open() as file:
        return {row["policy"]: row for row in json.load(file)}


def main():
    old = load_summary(OLD_SUMMARY)
    new = load_summary(EXPERIMENT_DIR / "analysis/summary.json")
    rows = []
    for policy in POLICIES:
        for window, source in ((90, old), (180, new)):
            rows.append({"arrival_window_s": window, **source[policy]})
    comparison = pd.DataFrame(rows)
    comparison.to_csv(EXPERIMENT_DIR / "analysis/comparison_with_90s.csv", index=False)

    x = np.arange(len(POLICIES))
    width = 0.34
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.2))
    metrics = [
        ("e2e_ttft_mean_ms", "mean E2E TTFT (ms)"),
        ("router_queue_mean_ms", "mean router queue (ms)"),
        ("redirects", "redirected requests"),
    ]
    for axis, (metric, title) in zip(axes, metrics):
        values_90 = [old[policy][metric] for policy in POLICIES]
        values_180 = [new[policy][metric] for policy in POLICIES]
        axis.bar(x - width / 2, values_90, width, color="#9b9892", label="90s")
        axis.bar(x + width / 2, values_180, width, color="#3a6aaa", label="180s")
        axis.set_xticks(x, ["NEAREST_KV", "MIGRATE", "MIGRATE_KV"],
                        rotation=15, ha="right")
        axis.set_title(title)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle("Input 10000 / reuse 50%: 90s vs 180s arrival window", fontsize=18)
    fig.tight_layout()
    fig.savefig(EXPERIMENT_DIR / "figures/comparison_with_90s.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


if __name__ == "__main__":
    main()
