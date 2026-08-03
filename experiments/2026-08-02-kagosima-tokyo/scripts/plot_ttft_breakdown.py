#!/usr/bin/env python3
"""Plot the TTFT breakdown for the All-Tokyo PP=2+spec heavy run."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
INPUT_PATH = (
    EXPERIMENT_DIR
    / "results/all_tokyo_pp2_spec/heavy/requests.csv"
)
OUTPUT_DIR = EXPERIMENT_DIR / "analysis"

COMPONENTS = (
    ("queueing_before_ttft_ns", "Scheduler queue", "#e79b37"),
    ("prefill_service_ns", "Compute / prefill", "#31866f"),
    ("communication_latency_ns", "RTT / communication", "#4f83c2"),
)


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values), q))


def component_stat(rows: list[dict[str, str]], key: str, stat: str) -> float:
    values = [float(row[key]) / 1e6 for row in rows]
    if stat == "Mean":
        return float(np.mean(values))
    if stat == "Median":
        return percentile(values, 50)
    return percentile(values, 99)


def main() -> None:
    with INPUT_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    by_instance: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_instance[int(row["instance id"])].append(row)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = OUTPUT_DIR / "all_tokyo_pp2_spec_heavy_ttft_breakdown.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["scope", "n", "stat", "queueing_ms", "prefill_ms",
                         "communication_ms", "component_sum_ms", "e2e_ttft_ms"])
        scopes = [("Overall", rows)] + [
            (f"Instance {instance_id}", instance_rows)
            for instance_id, instance_rows in sorted(by_instance.items())
        ]
        for scope, scope_rows in scopes:
            for stat in ("Mean", "Median", "P99"):
                parts = [component_stat(scope_rows, key, stat) for key, _, _ in COMPONENTS]
                e2e = component_stat(scope_rows, "e2e_ttft_ns", stat)
                writer.writerow([scope, len(scope_rows), stat, *parts, sum(parts), e2e])

    input_lengths = sorted({int(row["input"]) for row in rows})
    cohorts = [("All requests", rows)] + [
        (f"Input {input_length}", [
            row for row in rows if int(row["input"]) == input_length
        ])
        for input_length in input_lengths
    ]

    fig, axis = plt.subplots(figsize=(12.5, 7.5))
    positions = np.arange(len(cohorts)) * 1.12
    left = np.zeros(len(cohorts))
    totals = np.array([
        component_stat(cohort_rows, "e2e_ttft_ns", "Mean")
        for _, cohort_rows in cohorts
    ])
    maximum = float(totals.max())
    for key, label, color in COMPONENTS:
        widths = np.array([
            component_stat(cohort_rows, key, "Mean")
            for _, cohort_rows in cohorts
        ])
        axis.barh(positions, widths, left=left, height=0.72, color=color,
                  edgecolor="#faf8f4", linewidth=2, label=label)
        for position, start, width in zip(positions, left, widths):
            if width >= maximum * 0.055:
                axis.text(start + width / 2, position, f"{width:.1f} ms",
                          ha="center", va="center", color="white", fontsize=9,
                          fontweight="bold")
        left += widths
    for position, total, (_, cohort_rows) in zip(positions, totals, cohorts):
        axis.text(total + maximum * 0.018, position,
                  f"{total:.0f} ms (n={len(cohort_rows)})",
                  va="center", fontweight="bold", fontsize=9.5)
    axis.set_yticks(positions, [label for label, _ in cohorts])
    axis.set_ylim(positions[-1] + 0.7, -0.7)
    axis.set_xlim(0, maximum * 1.25)
    axis.set_xlabel("Mean E2E TTFT components (ms)")
    axis.set_title("All-Tokyo PP=2+spec, heavy load: mean E2E TTFT breakdown")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    fig.legend(loc="lower center", ncol=len(COMPONENTS), frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    breakdown_path = OUTPUT_DIR / "all_tokyo_pp2_spec_heavy_mean_ttft_breakdown.png"
    fig.savefig(breakdown_path, dpi=200, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    fig, axes = plt.subplots(3, 2, figsize=(13, 14), squeeze=False)
    for axis, input_length in zip(axes.flat, input_lengths):
        values = np.sort(np.array([
            float(row["e2e_ttft_ns"]) / 1e6
            for row in rows if int(row["input"]) == input_length
        ]))
        cdf = np.arange(1, len(values) + 1) / len(values)
        axis.plot(values, cdf, linewidth=2.5, color="#4c78a8",
                  label=f"All-Tokyo PP=2+spec (n={len(values)})")
        p50 = float(np.percentile(values, 50))
        p99 = float(np.percentile(values, 99))
        axis.axvline(p50, color="#54a24b", linestyle="--", linewidth=1.3,
                     label=f"p50 {p50:.0f} ms")
        axis.axvline(p99, color="#e45756", linestyle=":", linewidth=1.5,
                     label=f"p99 {p99:.0f} ms")
        axis.set_title(f"Input {input_length}")
        axis.set_xlabel("E2E TTFT (ms)")
        axis.set_ylabel("CDF")
        axis.set_ylim(-0.02, 1.03)
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.legend(frameon=False, fontsize=8, loc="lower right")
    fig.suptitle("All-Tokyo PP=2+spec, heavy load: per-request E2E TTFT CDF",
                 fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    cdf_path = OUTPUT_DIR / "all_tokyo_pp2_spec_heavy_ttft_cdf.png"
    fig.savefig(cdf_path, dpi=200, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    print(breakdown_path)
    print(cdf_path)
    print(summary_path)


if __name__ == "__main__":
    main()
