#!/usr/bin/env python3
"""Ablation of the three axes behind Multi no-model's win over KV migrate /
Multi learned (see reports/final_three_policy_analysis.md):
  1. candidate count   -- Dynamic (1 candidate) vs Multi (N candidates)
  2. model-based gate  -- whether the local-wait-vs-redirect comparison uses
                           the TTFT formula at all (no-model always redirects
                           once a candidate is admissible)
  3. ranking method     -- capacity pressure vs TTFT-formula prediction
  4. reservation        -- atomic target KV/slot reservation, on vs off

Scoped to the two rate=3.33 seeds that actually produce enough redirects to
carry a signal (rate=2.5 seeds have 2-6 redirects and show no separation
between policies -- see run_ablation.sh).
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures" / "ablation"
ANALYSIS = ROOT / "analysis"
REPORTS = ROOT / "reports"

SEEDS = ["mixed_rate3p33_seed1", "mixed_rate3p33_seed2"]
SEED_LABELS = {"mixed_rate3p33_seed1": "seed 1", "mixed_rate3p33_seed2": "seed 2"}

# (label, dir_name, color, note)
ALL_POLICIES = [
    ("KV migrate", "NEAREST_MIGRATE_KV", "#d18120", None),
    ("Dynamic (1 candidate)", "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE", "#c74b3a", None),
    ("Multi no-model", "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE", "#b03a8e", None),
    ("Multi no-model, no-reserve", "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE_NO_RESERVATION", "#b03a8e",
     "reservation ablation pair"),
    ("Multi pressure+gate", "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", "#3f8f7a", None),
    ("Multi learned", "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE", "#5a4fcf", None),
    ("Multi learned, no-reserve", "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_NO_RESERVATION", "#5a4fcf",
     "reservation ablation pair"),
]

# The five outcome-distinct policies shown in the main bar/breakdown/CDF charts.
CHART_POLICIES = [p for p in ALL_POLICIES if p[3] is None]

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]

REFERENCE_QUANTILES = [0.5, 0.9, 0.95, 0.99]


def load(seed, dir_name):
    path = RESULTS / seed / dir_name / "requests.csv"
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def mean_ms(rows, column):
    return sum(float(row[column]) for row in rows) / len(rows) / 1e6


def quantile_ms(rows, q):
    values = sorted(float(row["e2e_ttft_ns"]) / 1e6 for row in rows)
    return float(np.quantile(values, q))


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


def summary_rows():
    rows = []
    for seed in SEEDS:
        for label, dir_name, color, note in ALL_POLICIES:
            requests = load(seed, dir_name)
            redirects = sum(1 for r in requests if int(float(r["rerouted"])) == 1)
            values = breakdown(requests)
            rows.append({
                "seed": seed,
                "policy_label": label,
                "policy_dir": dir_name,
                "note": note or "",
                "request_count": len(requests),
                "redirects": redirects,
                "mean_ms": values["Total"],
                "p50_ms": quantile_ms(requests, 0.5),
                "p90_ms": quantile_ms(requests, 0.9),
                "p95_ms": quantile_ms(requests, 0.95),
                "p99_ms": quantile_ms(requests, 0.99),
                **{f"component_{name}": values[name] for name, _ in COMPONENTS},
            })
    return rows


def write_summary_csv(rows):
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    output = ANALYSIS / "ablation_summary.csv"
    fields = ["seed", "policy_label", "policy_dir", "note", "request_count", "redirects",
              "mean_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms",
              *[f"component_{name}" for name, _ in COMPONENTS]]
    with output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {output}")


def plot_ttft_by_policy(rows):
    figure, axes = plt.subplots(1, 2, figsize=(13, 5.6), sharey=True)
    maximum = max(r["mean_ms"] for r in rows if r["note"] == "")
    for axis, seed in zip(axes, SEEDS):
        entries = [r for r in rows if r["seed"] == seed and r["note"] == ""]
        entries = [r for label, _, _, _ in CHART_POLICIES for r in entries if r["policy_label"] == label]
        positions = list(range(len(entries)))
        colors = [dict((l, c) for l, _, c, _ in CHART_POLICIES)[r["policy_label"]] for r in entries]
        widths = [r["mean_ms"] for r in entries]
        axis.barh(positions, widths, color=colors, height=0.6, edgecolor="#faf8f4", linewidth=1.2)
        for position, r in zip(positions, entries):
            axis.text(r["mean_ms"] + maximum * 0.015, position,
                      f'{r["mean_ms"]:.1f} ms (n redirects={r["redirects"]})',
                      va="center", fontsize=8.5, fontweight="bold")
        axis.set_yticks(positions, [r["policy_label"] for r in entries], fontsize=9.5)
        axis.invert_yaxis()
        axis.set_title(SEED_LABELS[seed], fontsize=12)
        axis.set_xlabel("mean E2E TTFT (ms)")
        axis.set_xlim(0, maximum * 1.35)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    figure.suptitle("Ablation: mean E2E TTFT by policy (mixed_rate3p33)", fontsize=15)
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    output = FIGURES / "ttft_by_policy.png"
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    print(f"wrote {output}")


def plot_reservation_ablation(rows):
    pairs = [
        ("Multi no-model", "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE",
         "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE_NO_RESERVATION"),
        ("Multi learned", "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE",
         "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_NO_RESERVATION"),
    ]
    figure, axis = plt.subplots(figsize=(9, 5))
    labels = []
    reserve_vals = []
    noreserve_vals = []
    for policy_label, reserve_dir, noreserve_dir in pairs:
        for seed in SEEDS:
            reserve_row = next(r for r in rows if r["seed"] == seed and r["policy_dir"] == reserve_dir)
            noreserve_row = next(r for r in rows if r["seed"] == seed and r["policy_dir"] == noreserve_dir)
            labels.append(f"{policy_label}\n{SEED_LABELS[seed]}")
            reserve_vals.append(reserve_row["mean_ms"])
            noreserve_vals.append(noreserve_row["mean_ms"])
    positions = np.arange(len(labels))
    width = 0.32
    axis.bar(positions - width / 2, reserve_vals, width, label="reservation on",
              color="#31866f", edgecolor="#faf8f4", linewidth=1.2)
    axis.bar(positions + width / 2, noreserve_vals, width, label="reservation off",
              color="#c9a24a", edgecolor="#faf8f4", linewidth=1.2)
    for position, on, off in zip(positions, reserve_vals, noreserve_vals):
        axis.text(position, max(on, off) + 12, f"Δ={on - off:+.4f} ms",
                  ha="center", fontsize=8, color="#555")
    axis.set_xticks(positions, labels, fontsize=9)
    axis.set_ylabel("mean E2E TTFT (ms)")
    axis.set_title("Reservation ablation: on vs off (mixed_rate3p33)", fontsize=13)
    axis.legend(frameon=False, loc="lower right")
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    output = FIGURES / "reservation_ablation.png"
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    print(f"wrote {output}")


def plot_breakdown(seed):
    figure, axis = plt.subplots(figsize=(10, 5.6))
    entries = []
    for label, dir_name, color, note in CHART_POLICIES:
        requests = load(seed, dir_name)
        entries.append((label, breakdown(requests), len(requests)))
    positions = list(range(len(entries)))
    left = [0.0] * len(entries)
    for component, component_color in COMPONENTS:
        widths = [values[component] for _, values, _ in entries]
        axis.barh(positions, widths, left=left, height=0.58, color=component_color,
                  edgecolor="#faf8f4", linewidth=1.2, label=component)
        left = [old + width for old, width in zip(left, widths)]
    maximum = max(values["Total"] for _, values, _ in entries)
    for position, (label, values, n) in enumerate(entries):
        axis.text(values["Total"] + maximum * 0.015, position,
                  f'{values["Total"]:.1f} ms', va="center", fontsize=9, fontweight="bold")
    axis.set_yticks(positions, [label for label, _, _ in entries], fontsize=10)
    axis.invert_yaxis()
    axis.set_xlabel("mean E2E TTFT components (ms)")
    axis.set_xlim(0, maximum * 1.25)
    axis.set_title(f"TTFT breakdown by policy: {SEED_LABELS[seed]} (mixed_rate3p33)", fontsize=13)
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, fontsize=8.5, ncol=1)
    figure.tight_layout()
    output = FIGURES / f"ttft_breakdown_{seed}.png"
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    print(f"wrote {output}")


def plot_cdf(seed):
    figure, axis = plt.subplots(figsize=(9, 6))
    axis_max = 1.0
    axis_min = float("inf")
    for label, dir_name, color, note in CHART_POLICIES:
        requests = load(seed, dir_name)
        values = np.sort(np.array([float(r["e2e_ttft_ns"]) for r in requests]) / 1e6)
        fractions = np.arange(1, len(values) + 1) / len(values)
        axis_max = max(axis_max, values.max())
        axis_min = min(axis_min, values.min())
        axis.step(values, fractions, where="post", color=color, linewidth=2, label=label)
    for q in REFERENCE_QUANTILES:
        axis.axhline(q, color="#c9c2b6", linewidth=0.8, linestyle=(0, (2, 2)), zorder=0)
        axis.text(0.995, q, f"p{int(q * 100)}", transform=axis.get_yaxis_transform(),
                  va="center", ha="right", fontsize=7.5, color="#8a8378")
    axis.set_xscale("log")
    axis.set_xlim(10 ** np.floor(np.log10(axis_min)), 10 ** np.ceil(np.log10(axis_max)))
    axis.set_ylim(0, 1.02)
    axis.set_xlabel("E2E TTFT (ms, log scale)")
    axis.set_ylabel("cumulative fraction of requests")
    axis.set_title(f"E2E TTFT CDF by policy: {SEED_LABELS[seed]} (mixed_rate3p33)", fontsize=13)
    axis.grid(axis="y", color="#e3ded4", linewidth=0.7)
    axis.grid(axis="x", which="major", color="#e3ded4", linewidth=0.7)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, fontsize=8.5)
    figure.tight_layout()
    output = FIGURES / f"ttft_cdf_{seed}.png"
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    print(f"wrote {output}")


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    rows = summary_rows()
    write_summary_csv(rows)
    plot_ttft_by_policy(rows)
    plot_reservation_ablation(rows)
    for seed in SEEDS:
        plot_breakdown(seed)
        plot_cdf(seed)
    print("done")


if __name__ == "__main__":
    main()
