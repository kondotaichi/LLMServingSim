#!/usr/bin/env python3
"""Plot the prompt-6000, 120-second comparison across three policies."""

import csv
import math
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
METHODS = [
    ("A: wait + local KV", "NEAREST_KV", EXPERIMENT_DIR / "results/NEAREST_KV/requests.csv", "#2f5f9f"),
    ("B: GPU forward + cold prefill", "NEAREST_MIGRATE", EXPERIMENT_DIR / "results/NEAREST_MIGRATE/requests.csv", "#d18120"),
    ("C: GPU forward + KV handoff", "NEAREST_MIGRATE_KV", EXPERIMENT_DIR / "results/NEAREST_MIGRATE_KV/requests.csv", "#31866f"),
]


def read_rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def values_ms(rows, column):
    return [float(row[column]) / 1e6 for row in rows]


def mean(values):
    return sum(values) / len(values)


def percentile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - position) + values[upper] * (position - lower)


def get_breakdown(rows):
    e2e = mean(values_ms(rows, "e2e_ttft_ns"))
    compute = mean(values_ms(rows, "prefill_service_ns"))
    communication = mean(values_ms(rows, "communication_latency_ns"))
    scheduler_queue = mean(values_ms(rows, "queueing_before_ttft_ns"))
    kv_transfer = mean(values_ms(rows, "kv_migration_latency_ns"))
    other_communication = max(0.0, communication - kv_transfer)
    router_queue = max(0.0, e2e - compute - communication - scheduler_queue)
    return {
        "Router queue": router_queue,
        "Scheduler queue": scheduler_queue,
        "KV transfer": kv_transfer,
        "Compute / prefill": compute,
        "RTT / other comm": other_communication,
        "Total": e2e,
    }


def plot_breakdown(rows_by_method, output):
    breakdowns = {name: get_breakdown(rows) for name, rows in rows_by_method.items()}
    components = [
        ("Router queue", "#c74b3a"),
        ("Scheduler queue", "#e79b37"),
        ("KV transfer", "#865bd6"),
        ("Compute / prefill", "#31866f"),
        ("RTT / other comm", "#4f83c2"),
    ]
    positions = list(range(len(METHODS)))
    labels = [label for label, _, _, _ in METHODS]
    left = [0.0] * len(METHODS)
    fig, axis = plt.subplots(figsize=(15, 7.2))

    for component, color in components:
        widths = [breakdowns[name][component] for _, name, _, _ in METHODS]
        bars = axis.barh(positions, widths, left=left, height=0.58, color=color,
                         edgecolor="#faf8f4", linewidth=2, label=component)
        for index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= 100:
                axis.text(left[index] + width / 2, bar.get_y() + bar.get_height() / 2,
                          f"{width:.0f}ms", ha="center", va="center", color="white",
                          fontsize=10.5, fontweight="bold")
        left = [old + width for old, width in zip(left, widths)]

    maximum = max(breakdowns[name]["Total"] for _, name, _, _ in METHODS)
    for position, (_, name, _, _) in zip(positions, METHODS):
        total = breakdowns[name]["Total"]
        axis.text(total + maximum * 0.018, position, f"{total:.0f}ms", ha="left",
                  va="center", fontsize=11.5, fontweight="bold", color="#262421")

    rtt = " | ".join(f"{name}: {breakdowns[name]['RTT / other comm']:.2f}ms"
                     for _, name, _, _ in METHODS)
    axis.text(0.985, 0.97, f"RTT / other comm mean: {rtt}", transform=axis.transAxes,
              ha="right", va="top", fontsize=9, color="#4f83c2")
    axis.set_yticks(positions, labels=labels, fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.14)
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title("Prompt 6000 / 120s: mean E2E TTFT breakdown\nThree nearest-GPU policies",
                   fontsize=17, pad=18)
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, ncol=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)
    return breakdowns


def write_handoff_breakdown(rows_by_method, image_output, csv_output):
    """Compare all requests with the redirected subsets for policies B and C."""
    series = [
        ("A: all", rows_by_method["NEAREST_KV"]),
        ("B: all", rows_by_method["NEAREST_MIGRATE"]),
        ("B: redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 1
        ]),
        ("B: not redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 0
        ]),
        ("C: all", rows_by_method["NEAREST_MIGRATE_KV"]),
        ("C: handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 1
        ]),
        ("C: no handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 0
        ]),
    ]
    if any(not rows for _, rows in series):
        raise ValueError("Cannot plot handoff breakdown: one or more subsets are empty")

    breakdowns = {label: get_breakdown(rows) for label, rows in series}
    components = [
        ("Router queue", "#c74b3a"),
        ("Scheduler queue", "#e79b37"),
        ("KV transfer", "#865bd6"),
        ("Compute / prefill", "#31866f"),
        ("RTT / other comm", "#4f83c2"),
    ]
    positions = list(range(len(series)))
    left = [0.0] * len(series)
    fig, axis = plt.subplots(figsize=(15, 10.2))
    for component, color in components:
        widths = [breakdowns[label][component] for label, _ in series]
        bars = axis.barh(positions, widths, left=left, height=0.58, color=color,
                         edgecolor="#faf8f4", linewidth=2, label=component)
        for index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= 100:
                axis.text(left[index] + width / 2, bar.get_y() + bar.get_height() / 2,
                          f"{width:.0f}ms", ha="center", va="center", color="white",
                          fontsize=10.5, fontweight="bold")
        left = [old + width for old, width in zip(left, widths)]

    maximum = max(values["Total"] for values in breakdowns.values())
    for position, (label, rows) in zip(positions, series):
        total = breakdowns[label]["Total"]
        axis.text(total + maximum * 0.018, position, f"{total:.0f}ms (n={len(rows)})",
                  ha="left", va="center", fontsize=11, fontweight="bold", color="#262421")
    axis.set_yticks(positions, labels=[label for label, _ in series], fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.18)
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title("Prompt 6000 / 120s: E2E TTFT breakdown\nAll, redirected, and non-redirected requests",
                   fontsize=17, pad=18)
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, ncol=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(image_output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    with csv_output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["series", "request_count", *[name for name, _ in components], "Total"])
        for label, rows in series:
            writer.writerow([label, len(rows), *[
                f"{breakdowns[label][name]:.6f}" for name, _ in components
            ], f"{breakdowns[label]['Total']:.6f}"])
    return breakdowns


def handoff_series(rows_by_method):
    """Return the seven populations used by the handoff comparison plots."""
    return [
        ("A: all", rows_by_method["NEAREST_KV"], "#2f5f9f"),
        ("B: all", rows_by_method["NEAREST_MIGRATE"], "#d18120"),
        ("B: redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 1
        ], "#e7a34d"),
        ("B: not redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 0
        ], "#f2c879"),
        ("C: all", rows_by_method["NEAREST_MIGRATE_KV"], "#31866f"),
        ("C: handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 1
        ], "#65ad98"),
        ("C: no handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 0
        ], "#9bcdbf"),
    ]


def plot_handoff_boxplot(rows_by_method, output, metric, title, xlabel):
    """Plot one request-level metric for the seven handoff populations."""
    series = handoff_series(rows_by_method)
    if any(not rows for _, rows, _ in series):
        raise ValueError("Cannot plot handoff boxplot: one or more subsets are empty")

    def metric_values(rows):
        if metric == "e2e_ttft":
            return values_ms(rows, "e2e_ttft_ns")
        if metric == "queue_wait":
            return [
                max(0.0, (
                    float(row["e2e_ttft_ns"])
                    - float(row["prefill_service_ns"])
                    - float(row["communication_latency_ns"])
                ) / 1e6)
                for row in rows
            ]
        if metric == "compute_prefill":
            return values_ms(rows, "prefill_service_ns")
        raise ValueError(f"Unknown boxplot metric: {metric}")

    data = [metric_values(rows) for _, rows, _ in series]
    labels = [f"{label} (n={len(rows)})" for label, rows, _ in series]
    colors = [color for _, _, color in series]
    fig, axis = plt.subplots(figsize=(14, 10.2))
    boxes = axis.boxplot(
        data,
        vert=False,
        tick_labels=labels,
        widths=0.58,
        whis=1.5,
        patch_artist=True,
        showmeans=True,
        meanprops={"marker": "D", "markerfacecolor": "white",
                   "markeredgecolor": "#262421", "markersize": 5},
        medianprops={"color": "#fffdf8", "linewidth": 2.2},
        whiskerprops={"color": "#56514b", "linewidth": 1.3},
        capprops={"color": "#56514b", "linewidth": 1.3},
        flierprops={"marker": "o", "markerfacecolor": "#56514b",
                    "markeredgecolor": "none", "markersize": 3, "alpha": 0.35},
    )
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_edgecolor("#faf8f4")
        patch.set_linewidth(1.5)

    axis.invert_yaxis()
    axis.set_xlabel(xlabel, fontsize=12)
    axis.set_title(title, fontsize=17, pad=18)
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(axis="y", labelsize=11.5)
    axis.text(0.99, 0.02, "diamond: mean | center line: median | whiskers: 1.5 IQR",
              transform=axis.transAxes, ha="right", va="bottom", fontsize=9,
              color="#56514b")
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_handoff_cdf(rows_by_method, output):
    """Plot E2E TTFT CDFs for all requests and the redirected subsets."""
    series = [
        ("A: all", rows_by_method["NEAREST_KV"], "#2f5f9f", "-"),
        ("B: all", rows_by_method["NEAREST_MIGRATE"], "#d18120", "-"),
        ("B: redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 1
        ], "#d18120", "--"),
        ("B: not redirected only", [
            row for row in rows_by_method["NEAREST_MIGRATE"]
            if int(float(row["rerouted"])) == 0
        ], "#d18120", ":"),
        ("C: all", rows_by_method["NEAREST_MIGRATE_KV"], "#31866f", "-"),
        ("C: handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 1
        ], "#31866f", "--"),
        ("C: no handoff only", [
            row for row in rows_by_method["NEAREST_MIGRATE_KV"]
            if int(float(row["rerouted"])) == 0
        ], "#31866f", ":"),
    ]
    if any(not rows for _, rows, _, _ in series):
        raise ValueError("Cannot plot handoff CDF: one or more subsets are empty")

    fig, axis = plt.subplots(figsize=(14, 7.5))
    for label, rows, color, linestyle in series:
        values = sorted(values_ms(rows, "e2e_ttft_ns"))
        probabilities = [(index + 1) / len(values) for index in range(len(values))]
        axis.step(values, probabilities, where="post", linewidth=2.4, color=color,
                  linestyle=linestyle, label=f"{label} (n={len(rows)})")
    axis.set_xscale("log")
    axis.set_ylim(0, 1.02)
    axis.set_xlabel("E2E TTFT (ms, log scale)", fontsize=12)
    axis.set_ylabel("cumulative probability", fontsize=12)
    axis.set_title("Prompt 6000 / 120s: E2E TTFT CDF\nAll, redirected, and non-redirected requests",
                   fontsize=17, pad=16)
    axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cdf(rows_by_method, output):
    fig, axis = plt.subplots(figsize=(14, 7.2))
    levels = [0.91, 0.855, 0.80]
    for level, (_, name, _, color) in zip(levels, METHODS):
        values = sorted(values_ms(rows_by_method[name], "e2e_ttft_ns"))
        probabilities = [(index + 1) / len(values) for index in range(len(values))]
        axis.step(values, probabilities, where="post", linewidth=2.4, color=color, label=name)
        p50 = percentile(values, 0.50)
        p95 = percentile(values, 0.95)
        axis.scatter([p50, p95], [0.50, 0.95], color=color, s=34, zorder=3)
        axis.annotate(f"{name}: p50 {p50:.0f}ms / p95 {p95 / 1000:.1f}s",
                      xy=(p95, 0.95), xytext=(0.985, level), textcoords="axes fraction",
                      ha="right", color=color, fontsize=10)
    axis.set_xscale("log")
    axis.set_ylim(0, 1.02)
    axis.set_xlabel("E2E TTFT (ms, log scale)", fontsize=12)
    axis.set_ylabel("cumulative probability", fontsize=12)
    axis.set_title("Prompt 6000 / 120s: E2E TTFT CDF\nThree nearest-GPU policies",
                   fontsize=17, pad=16)
    axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_gpu_load(rows_by_method, output):
    fig, axis = plt.subplots(figsize=(14, 6.5))
    gpu_ids = list(range(10))
    width = 0.25
    offsets = [-width, 0.0, width]
    for offset, (_, name, _, color) in zip(offsets, METHODS):
        counts = Counter(int(float(row["gpu_id"])) for row in rows_by_method[name])
        axis.bar([gpu + offset for gpu in gpu_ids], [counts[gpu] for gpu in gpu_ids],
                 width=width, color=color, label=name)
    axis.set_xticks(gpu_ids)
    axis.set_xlabel("GPU / cell ID", fontsize=12)
    axis.set_ylabel("processed requests", fontsize=12)
    axis.set_title("Prompt 6000 / 120s: per-GPU processed request count", fontsize=17, pad=14)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(fontsize=10.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    output_dir = EXPERIMENT_DIR / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_by_method = {name: read_rows(path) for _, name, path, _ in METHODS}
    prefix = "three_policy"
    breakdown_output = output_dir / f"{prefix}_ttft_breakdown.png"
    cdf_output = output_dir / f"{prefix}_ttft_cdf.png"
    load_output = output_dir / f"{prefix}_gpu_load.png"
    handoff_breakdown_output = output_dir / f"{prefix}_handoff_only_ttft_breakdown.png"
    handoff_breakdown_csv = output_dir / f"{prefix}_handoff_only_ttft_breakdown.csv"
    handoff_cdf_output = output_dir / f"{prefix}_handoff_only_ttft_cdf.png"
    handoff_ttft_boxplot_output = output_dir / f"{prefix}_handoff_only_ttft_boxplot.png"
    handoff_queue_boxplot_output = output_dir / f"{prefix}_handoff_only_queue_boxplot.png"
    handoff_compute_boxplot_output = output_dir / f"{prefix}_handoff_only_compute_boxplot.png"
    breakdowns = plot_breakdown(rows_by_method, breakdown_output)
    write_handoff_breakdown(rows_by_method, handoff_breakdown_output, handoff_breakdown_csv)
    plot_handoff_cdf(rows_by_method, handoff_cdf_output)
    plot_handoff_boxplot(
        rows_by_method, handoff_ttft_boxplot_output, "e2e_ttft",
        "Prompt 6000 / 120s: E2E TTFT distribution\nAll, redirected, and non-redirected requests",
        "E2E TTFT (ms)")
    plot_handoff_boxplot(
        rows_by_method, handoff_queue_boxplot_output, "queue_wait",
        "Prompt 6000 / 120s: Queue / wait distribution\nAll, redirected, and non-redirected requests",
        "Queue / wait residual (ms)")
    plot_handoff_boxplot(
        rows_by_method, handoff_compute_boxplot_output, "compute_prefill",
        "Prompt 6000 / 120s: Compute / prefill distribution\nAll, redirected, and non-redirected requests",
        "Compute / prefill (ms)")
    plot_cdf(rows_by_method, cdf_output)
    plot_gpu_load(rows_by_method, load_output)
    for name, values in breakdowns.items():
        print(name, {key: round(value, 3) for key, value in values.items()})
    print(breakdown_output)
    print(cdf_output)
    print(load_output)
    print(handoff_breakdown_output)
    print(handoff_breakdown_csv)
    print(handoff_cdf_output)
    print(handoff_ttft_boxplot_output)
    print(handoff_queue_boxplot_output)
    print(handoff_compute_boxplot_output)


if __name__ == "__main__":
    main()
