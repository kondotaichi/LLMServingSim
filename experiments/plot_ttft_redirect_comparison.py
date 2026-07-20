#!/usr/bin/env python3
"""Regenerate TTFT breakdown and CDF figures split by redirect status."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
EXPERIMENTS = [
    {
        "directory": ROOT / "2026-07-14_prompt6000_90s_three_policy_良結果",
        "title": "Prompt 6000 / 90s",
        "breakdown": "three_policy_ttft_breakdown.png",
        "cdf": "three_policy_ttft_cdf.png",
    },
    {
        "directory": ROOT / "2026-07-16_input10000_reuse05_180s_three_policy_良結果",
        "title": "Input 10000 / reuse 50% / 180s",
        "breakdown": "seven_series_ttft_breakdown.png",
        "cdf": "seven_series_ttft_cdf.png",
    },
]

METHODS = [
    ("Nearest KV", "NEAREST_KV", "#2f5f9f"),
    ("Migrate", "NEAREST_MIGRATE", "#d18120"),
    ("Migrate + KV", "NEAREST_MIGRATE_KV", "#31866f"),
    ("Second TTFT reserve", "NEAREST_SECOND_TTFT_RESERVE_T160K_I1M", "#8b5fbf"),
    ("Capacity oneshot", "NEAREST_CAPACITY_ONESHOT_KV_RESERVE_T160K_I1M_M200MS_D1S", "#cc5c5c"),
    ("Capacity formula", "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_MODEL20260716", "#4f8fba"),
]

SUBSETS = [
    ("All requests", lambda row: True),
    ("Redirected only", lambda row: int(float(row["rerouted"])) == 1),
    ("Not redirected only", lambda row: int(float(row["rerouted"])) == 0),
]

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def read_methods(experiment_dir):
    runs = []
    for label, name, color in METHODS:
        path = experiment_dir / "results" / name / "requests.csv"
        if not path.is_file():
            print(f"skip {experiment_dir.name}/{name}: requests.csv is missing")
            continue
        with path.open(newline="") as file:
            runs.append((label, name, color, list(csv.DictReader(file))))
    return runs


def mean_ms(rows, column):
    return sum(float(row[column]) for row in rows) / len(rows) / 1e6


def breakdown(rows):
    total = mean_ms(rows, "e2e_ttft_ns")
    compute = mean_ms(rows, "prefill_service_ns")
    communication = mean_ms(rows, "communication_latency_ns")
    scheduler = mean_ms(rows, "queueing_before_ttft_ns")
    transfer = mean_ms(rows, "kv_migration_latency_ns")
    return {
        "Router queue": max(0.0, total - compute - communication - scheduler),
        "Scheduler queue": scheduler,
        "KV transfer": transfer,
        "Compute / prefill": compute,
        "RTT / other comm": max(0.0, communication - transfer),
        "Total": total,
    }


def subset_rows(rows, predicate):
    return [row for row in rows if predicate(row)]


def plot_breakdowns(config, runs):
    figure, axes = plt.subplots(1, 3, figsize=(23, 8.5), sharex=True)
    csv_rows = []
    maximum = 0.0
    panels = []
    for subset_label, predicate in SUBSETS:
        entries = []
        for method_label, method_name, color, rows in runs:
            selected = subset_rows(rows, predicate)
            values = breakdown(selected) if selected else None
            if values:
                maximum = max(maximum, values["Total"])
            entries.append((method_label, method_name, color, selected, values))
        panels.append((subset_label, entries))

    for axis, (subset_label, entries) in zip(axes, panels):
        positions = list(range(len(entries)))
        left = [0.0] * len(entries)
        for component, component_color in COMPONENTS:
            widths = [values[component] if values else 0.0
                      for _, _, _, _, values in entries]
            axis.barh(positions, widths, left=left, height=0.58,
                      color=component_color, edgecolor="#faf8f4", linewidth=1.2,
                      label=component)
            left = [old + width for old, width in zip(left, widths)]
        for position, (label, name, _, rows, values) in enumerate(entries):
            if values:
                axis.text(values["Total"] + maximum * 0.015, position,
                          f'{values["Total"]:.0f} ms\n(n={len(rows)})',
                          va="center", fontsize=8.5, fontweight="bold")
                csv_rows.append({
                    "method": name,
                    "method_label": label,
                    "subset": subset_label,
                    "request_count": len(rows),
                    **values,
                })
            else:
                axis.text(maximum * 0.015, position, "no requests", va="center",
                          fontsize=9, color="#777777")
        axis.set_yticks(positions, [entry[0] for entry in entries], fontsize=9.5)
        axis.invert_yaxis()
        axis.set_title(subset_label, fontsize=14)
        axis.set_xlabel("mean E2E TTFT components (ms)")
        axis.set_xlim(0, maximum * 1.25)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)

    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS),
                  frameon=False)
    figure.suptitle(f'{config["title"]}: mean E2E TTFT breakdown by redirect status',
                    fontsize=18)
    figure.tight_layout(rect=(0, 0.07, 1, 0.94))
    output = config["directory"] / "figures" / config["breakdown"]
    figure.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)

    csv_output = config["directory"] / "analysis" / "ttft_breakdown_by_redirect.csv"
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["method", "method_label", "subset", "request_count",
              *[name for name, _ in COMPONENTS], "Total"]
    with csv_output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(csv_rows)
    return output, csv_output


def plot_cdfs(config, runs):
    figure, axes = plt.subplots(1, 3, figsize=(22, 6.8), sharex=True, sharey=True)
    for axis, (subset_label, predicate) in zip(axes, SUBSETS):
        for method_label, _, color, rows in runs:
            selected = subset_rows(rows, predicate)
            if not selected:
                continue
            values = sorted(float(row["e2e_ttft_ns"]) / 1e6 for row in selected)
            probabilities = [(index + 1) / len(values) for index in range(len(values))]
            axis.step(values, probabilities, where="post", linewidth=2.1,
                      color=color, label=f"{method_label} (n={len(values)})")
        axis.set_xscale("log")
        axis.set_ylim(0, 1.02)
        axis.set_title(subset_label, fontsize=14)
        axis.set_xlabel("E2E TTFT (ms, log scale)")
        axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(fontsize=8.5, loc="lower right")
    axes[0].set_ylabel("cumulative probability")
    figure.suptitle(f'{config["title"]}: E2E TTFT CDF by redirect status', fontsize=18)
    figure.tight_layout(rect=(0, 0, 1, 0.93))
    output = config["directory"] / "figures" / config["cdf"]
    figure.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    return output


def main():
    for config in EXPERIMENTS:
        (config["directory"] / "figures").mkdir(parents=True, exist_ok=True)
        runs = read_methods(config["directory"])
        if not runs:
            raise ValueError(f'No request results found in {config["directory"]}')
        breakdown_output, csv_output = plot_breakdowns(config, runs)
        cdf_output = plot_cdfs(config, runs)
        print(breakdown_output)
        print(cdf_output)
        print(csv_output)


if __name__ == "__main__":
    main()
