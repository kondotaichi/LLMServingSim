#!/usr/bin/env python3
"""Plot TTFT, TPOT, and TTLT for the peak-5x naive/proposed arms."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"
ARMS = (
    (
        "Naive (PP=1, no redirect)",
        "peak_5x_seed1_1_no_redirect",
        "#4c78a8",
    ),
    (
        "Proposed (PP=2 + KV migration)",
        "peak_5x_seed1_4_redirect_kv_pp2",
        "#e45756",
    ),
)
METRICS = (
    ("TTFT", "e2e_ttft_ns", 1e6, "TTFT (ms)"),
    ("TPOT", "TPOT", 1e6, "TPOT (ms)"),
    ("TTLT", "request_completion_latency_ns", 1e9, "TTLT (s)"),
)


def read_requests(result_name: str) -> list[dict[str, str]]:
    path = RESULTS / result_name / "requests.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No requests found: {path}")
    return rows


def percentile(values: np.ndarray, q: int) -> float:
    return float(np.percentile(values, q))


def main() -> None:
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    arm_rows = {label: read_requests(result_name) for label, result_name, _ in ARMS}
    values: dict[tuple[str, str], np.ndarray] = {}
    summary: list[dict[str, object]] = []
    for label, _, _ in ARMS:
        rows = arm_rows[label]
        for metric, field, divisor, _ in METRICS:
            samples = np.asarray([float(row[field]) / divisor for row in rows])
            values[(label, metric)] = samples
            summary.append({
                "arm": label,
                "metric": metric,
                "requests": len(samples),
                "mean": float(np.mean(samples)),
                "p50": percentile(samples, 50),
                "p95": percentile(samples, 95),
                "p99": percentile(samples, 99),
            })

    summary_path = ANALYSIS / "peak_5x_naive_vs_proposed_latency.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    colors = [color for _, _, color in ARMS]
    short_labels = ["Naive", "Proposed"]
    for column, (metric, _, _, axis_label) in enumerate(METRICS):
        metric_values = [values[(label, metric)] for label, _, _ in ARMS]
        means = np.asarray([float(np.mean(samples)) for samples in metric_values])
        p95s = np.asarray([percentile(samples, 95) for samples in metric_values])

        bars = axes[0, column].bar(
            short_labels,
            means,
            color=colors,
            width=0.62,
            edgecolor="white",
        )
        axes[0, column].errorbar(
            np.arange(2),
            means,
            yerr=np.vstack((np.zeros(2), p95s - means)),
            fmt="none",
            ecolor="#333333",
            capsize=6,
            linewidth=1.5,
            label="p95",
        )
        value_format = ".2f" if metric == "TTLT" else ".1f"
        axes[0, column].bar_label(
            bars,
            labels=[format(value, value_format) for value in means],
            padding=4,
            fontsize=10,
        )
        delta = (means[1] / means[0] - 1) * 100
        axes[0, column].set_title(f"{metric}: mean ({delta:+.1f}%)")
        axes[0, column].set_ylabel(axis_label)
        axes[0, column].grid(axis="y", alpha=0.25)

        for (label, _, color), samples in zip(ARMS, metric_values):
            ordered = np.sort(samples)
            cdf = np.arange(1, len(ordered) + 1) / len(ordered)
            axes[1, column].step(
                ordered,
                cdf,
                where="post",
                label=label,
                color=color,
                linewidth=2.2,
            )
        axes[1, column].set_xlabel(axis_label)
        axes[1, column].set_ylabel("CDF")
        axes[1, column].set_ylim(0, 1.01)
        axes[1, column].grid(alpha=0.25)

    handles, labels = axes[1, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.955),
        ncol=2,
        frameon=False,
    )
    fig.suptitle("Hongo peak 5x: naive vs. proposed latency", y=0.995, fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.90))

    png_path = FIGURES / "peak_5x_naive_vs_proposed_latency.png"
    svg_path = FIGURES / "peak_5x_naive_vs_proposed_latency.svg"
    fig.savefig(png_path, dpi=200)
    fig.savefig(svg_path)
    plt.close(fig)

    print(f"Saved {summary_path}")
    print(f"Saved {png_path}")
    print(f"Saved {svg_path}")


if __name__ == "__main__":
    main()
