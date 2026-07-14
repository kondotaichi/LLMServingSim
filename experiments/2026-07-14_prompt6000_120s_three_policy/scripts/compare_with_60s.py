#!/usr/bin/env python3
"""Compare the prompt-6000 120-second experiment with the 60-second run."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO_ROOT / "experiments/2026-07-13_prompt6000_three_policy"
POLICIES = ["NEAREST_KV", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV"]


def load_json(path):
    with path.open() as file:
        return json.load(file)


def percentage_change(new_value, old_value):
    return (new_value / old_value - 1.0) * 100.0


def build_comparison(old_summary, new_summary):
    comparison = {"baseline_window_s": 60, "current_window_s": 120, "policies": {}}
    for policy in POLICIES:
        old = old_summary["policies"][policy]
        new = new_summary["policies"][policy]
        comparison["policies"][policy] = {
            "mean_ttft_60s_ms": old["e2e_ttft_mean_ms"],
            "mean_ttft_120s_ms": new["e2e_ttft_mean_ms"],
            "mean_ttft_change_pct": percentage_change(
                new["e2e_ttft_mean_ms"], old["e2e_ttft_mean_ms"]
            ),
            "p95_ttft_60s_ms": old["e2e_ttft_p95_ms"],
            "p95_ttft_120s_ms": new["e2e_ttft_p95_ms"],
            "p95_ttft_change_pct": percentage_change(
                new["e2e_ttft_p95_ms"], old["e2e_ttft_p95_ms"]
            ),
            "completion_mean_60s_ms": old["completion_mean_ms"],
            "completion_mean_120s_ms": new["completion_mean_ms"],
            "completion_mean_change_pct": percentage_change(
                new["completion_mean_ms"], old["completion_mean_ms"]
            ),
            "redirects_60s": old["redirects"],
            "redirects_120s": new["redirects"],
            "redirect_change": new["redirects"] - old["redirects"],
        }
    return comparison


def plot_comparison(comparison, output):
    x = np.arange(len(POLICIES))
    width = 0.34
    fig, axes = plt.subplots(1, 3, figsize=(17, 6.2))

    mean_60 = [comparison["policies"][policy]["mean_ttft_60s_ms"] for policy in POLICIES]
    mean_120 = [comparison["policies"][policy]["mean_ttft_120s_ms"] for policy in POLICIES]
    p95_60 = [comparison["policies"][policy]["p95_ttft_60s_ms"] for policy in POLICIES]
    p95_120 = [comparison["policies"][policy]["p95_ttft_120s_ms"] for policy in POLICIES]
    redirects_60 = [comparison["policies"][policy]["redirects_60s"] for policy in POLICIES]
    redirects_120 = [comparison["policies"][policy]["redirects_120s"] for policy in POLICIES]

    panels = [
        (axes[0], mean_60, mean_120, "mean E2E TTFT (ms)"),
        (axes[1], p95_60, p95_120, "p95 E2E TTFT (ms)"),
        (axes[2], redirects_60, redirects_120, "redirected requests"),
    ]
    short_labels = ["NEAREST_KV", "MIGRATE", "MIGRATE_KV"]
    for axis, values_60, values_120, title in panels:
        axis.bar(x - width / 2, values_60, width, color="#9b9892", label="60s")
        axis.bar(x + width / 2, values_120, width, color="#3a6aaa", label="120s")
        axis.set_xticks(x, short_labels, rotation=15, ha="right")
        axis.set_title(title, fontsize=13)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False)
    fig.suptitle("Prompt 6000: 60s vs 120s arrival window", fontsize=18, y=1.02)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    old_summary = load_json(BASELINE_DIR / "analysis/summary.json")
    new_summary = load_json(EXPERIMENT_DIR / "analysis/summary.json")
    comparison = build_comparison(old_summary, new_summary)

    output_json = EXPERIMENT_DIR / "analysis/comparison_with_60s.json"
    output_figure = EXPERIMENT_DIR / "figures/comparison_with_60s.png"
    with output_json.open("w") as file:
        json.dump(comparison, file, indent=2)
    plot_comparison(comparison, output_figure)
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
