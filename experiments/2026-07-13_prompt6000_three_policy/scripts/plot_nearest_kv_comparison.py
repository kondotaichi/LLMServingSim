#!/usr/bin/env python3
"""Plot TTFT breakdown and CDF for the prompt-6000 KV routing comparison."""

import argparse
import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
METHODS = [
    (
        "A: wait + local KV",
        "NEAREST_KV",
        EXPERIMENT_DIR / "results/NEAREST_KV/requests.csv",
        "#2f5f9f",
    ),
    (
        "B: GPU forward + KV handoff",
        "NEAREST_MIGRATE_KV",
        EXPERIMENT_DIR / "results/NEAREST_MIGRATE_KV/requests.csv",
        "#31866f",
    ),
]


def read_rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def values_ms(rows, column):
    return [float(row[column]) / 1e6 for row in rows]


def mean(values):
    return sum(values) / len(values)


def percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def breakdown(rows):
    e2e = mean(values_ms(rows, "e2e_ttft_ns"))
    compute = mean(values_ms(rows, "prefill_service_ns"))
    communication = mean(values_ms(rows, "communication_latency_ns"))
    kv_transfer = mean(values_ms(rows, "kv_migration_latency_ns"))
    other_communication = max(0.0, communication - kv_transfer)

    # Router capacity waiting happens before Scheduler.add_request(), so it is
    # not fully represented by queueing_before_ttft_ns. The E2E residual is the
    # complete wait component and makes the stacked components sum exactly to
    # the measured mean E2E TTFT.
    queue_wait = max(0.0, e2e - compute - communication)
    return {
        "Queue / wait": queue_wait,
        "KV transfer": kv_transfer,
        "Compute / prefill": compute,
        "RTT / other comm": other_communication,
        "Total": e2e,
    }


def plot_breakdown(data, output):
    labels = [item[0] for item in METHODS]
    components = [
        ("Queue / wait", "#c74b3a"),
        ("KV transfer", "#865bd6"),
        ("Compute / prefill", "#31866f"),
        ("RTT / other comm", "#4f83c2"),
    ]

    fig, axis = plt.subplots(figsize=(15, 6.5))
    positions = list(range(len(labels)))
    left = [0.0] * len(labels)

    for component, color in components:
        widths = [data[method_name][component] for _, method_name, _, _ in METHODS]
        bars = axis.barh(
            positions,
            widths,
            left=left,
            height=0.56,
            color=color,
            edgecolor="#faf8f4",
            linewidth=2,
            label=component,
        )
        for index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= 45:
                axis.text(
                    left[index] + width / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{width:.0f}ms",
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=11,
                    fontweight="bold",
                )
        left = [old + width for old, width in zip(left, widths)]

    maximum = max(data[name]["Total"] for _, name, _, _ in METHODS)
    for position, (_, method_name, _, _) in zip(positions, METHODS):
        total = data[method_name]["Total"]
        axis.text(
            total + maximum * 0.018,
            position,
            f"{total:.0f}ms",
            ha="left",
            va="center",
            fontsize=12,
            fontweight="bold",
            color="#262421",
        )

    axis.set_yticks(positions, labels=labels, fontsize=12)
    axis.invert_yaxis()
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title(
        "Prompt 6000: mean E2E TTFT breakdown\n"
        "NEAREST_KV vs NEAREST_MIGRATE_KV",
        fontsize=17,
        pad=18,
    )
    axis.set_xlim(0, maximum * 1.13)
    rtt_summary = " | ".join(
        f"{name}: {data[name]['RTT / other comm']:.2f}ms"
        for _, name, _, _ in METHODS
    )
    axis.text(
        0.985,
        0.965,
        f"RTT / other comm mean: {rtt_summary}",
        transform=axis.transAxes,
        ha="right",
        va="top",
        fontsize=9.5,
        color="#4f83c2",
    )
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(
        loc="lower right",
        frameon=False,
        ncol=4,
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cdf(rows_by_method, output):
    fig, axis = plt.subplots(figsize=(14, 7))
    annotation_levels = [0.91, 0.84]

    for level, (_, method_name, _, color) in zip(annotation_levels, METHODS):
        values = sorted(values_ms(rows_by_method[method_name], "e2e_ttft_ns"))
        probabilities = [(index + 1) / len(values) for index in range(len(values))]
        axis.step(
            values,
            probabilities,
            where="post",
            linewidth=2.5,
            color=color,
            label=method_name,
        )
        p50 = percentile(values, 0.50)
        p95 = percentile(values, 0.95)
        axis.scatter([p50, p95], [0.50, 0.95], color=color, s=38, zorder=3)
        axis.annotate(
            f"{method_name}: p50 {p50:.0f}ms / p95 {p95 / 1000:.1f}s",
            xy=(p95, 0.95),
            xytext=(0.985, level),
            textcoords="axes fraction",
            ha="right",
            color=color,
            fontsize=10.5,
        )

    axis.set_xscale("log")
    axis.set_ylim(0, 1.02)
    axis.set_xlabel("E2E TTFT (ms, log scale)", fontsize=12)
    axis.set_ylabel("cumulative probability", fontsize=12)
    axis.set_title(
        "Prompt 6000: E2E TTFT CDF\n"
        "NEAREST_KV vs NEAREST_MIGRATE_KV",
        fontsize=17,
        pad=16,
    )
    axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right", frameon=True, fontsize=11)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXPERIMENT_DIR / "figures",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows_by_method = {}
    breakdown_by_method = {}
    for _, method_name, path, _ in METHODS:
        rows = read_rows(path)
        rows_by_method[method_name] = rows
        breakdown_by_method[method_name] = breakdown(rows)

    prefix = "nearest_kv_vs_migrate_kv"
    breakdown_output = args.output_dir / f"{prefix}_ttft_breakdown.png"
    cdf_output = args.output_dir / f"{prefix}_ttft_cdf.png"
    plot_breakdown(breakdown_by_method, breakdown_output)
    plot_cdf(rows_by_method, cdf_output)

    for method_name, values in breakdown_by_method.items():
        print(method_name)
        for key, value in values.items():
            print(f"  {key}: {value:.3f} ms")
    print(breakdown_output)
    print(cdf_output)


if __name__ == "__main__":
    main()
