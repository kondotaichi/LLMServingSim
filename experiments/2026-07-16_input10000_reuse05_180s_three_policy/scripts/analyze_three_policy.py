#!/usr/bin/env python3
"""Analyze the input-10000, 50%-reuse, 180-second three-policy result."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
CASE_TITLE = "Input 10000 / reuse 50% / 180s"
RESULT_DIR = EXPERIMENT_DIR / "results"
FIGURE_DIR = EXPERIMENT_DIR / "figures"
ANALYSIS_DIR = EXPERIMENT_DIR / "analysis"
POLICIES = [
    ("A: NEAREST_KV", "NEAREST_KV", "#2f5f9f", "-"),
    ("B: NEAREST_MIGRATE", "NEAREST_MIGRATE", "#d18120", "--"),
    ("C: NEAREST_MIGRATE_KV", "NEAREST_MIGRATE_KV", "#31866f", ":"),
]
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def load_runs():
    return {
        name: pd.read_csv(RESULT_DIR / name / "requests.csv").set_index("request id").sort_index()
        for _, name, _, _ in POLICIES
    }


def ms(frame, column):
    return frame[column] / 1e6


def router_queue_ms(frame):
    return (
        frame["e2e_ttft_ns"] - frame["prefill_service_ns"]
        - frame["communication_latency_ns"] - frame["queueing_before_ttft_ns"]
    ).clip(lower=0) / 1e6


def validate(runs):
    baseline = runs["NEAREST_KV"]
    for name, frame in runs.items():
        if not baseline.index.equals(frame.index):
            raise ValueError(f"Request ID mismatch: {name}")
        for column in ("input", "output", "request_send_time_ns", "user_id",
                       "nearest_gpu_id"):
            if not baseline[column].equals(frame[column]):
                raise ValueError(f"Workload mismatch in {column}: {name}")


def series(runs):
    b = runs["NEAREST_MIGRATE"]
    c = runs["NEAREST_MIGRATE_KV"]
    return [
        ("A: all", runs["NEAREST_KV"], "#2f5f9f", "-"),
        ("B: all", b, "#d18120", "-"),
        ("B: redirected only", b[b.rerouted == 1], "#d18120", "--"),
        ("B: not redirected only", b[b.rerouted == 0], "#d18120", ":"),
        ("C: all", c, "#31866f", "-"),
        ("C: redirected only", c[c.rerouted == 1], "#31866f", "--"),
        ("C: not redirected only", c[c.rerouted == 0], "#31866f", ":"),
    ]


def breakdown(frame):
    communication = ms(frame, "communication_latency_ns").mean()
    transfer = ms(frame, "kv_migration_latency_ns").mean()
    return {
        "Router queue": router_queue_ms(frame).mean(),
        "Scheduler queue": ms(frame, "queueing_before_ttft_ns").mean(),
        "KV transfer": transfer,
        "Compute / prefill": ms(frame, "prefill_service_ns").mean(),
        "RTT / other comm": max(0.0, communication - transfer),
        "Total": ms(frame, "e2e_ttft_ns").mean(),
    }


def plot_breakdown(runs):
    populations = series(runs)
    values = {label: breakdown(frame) for label, frame, _, _ in populations}
    positions = np.arange(len(populations))
    left = np.zeros(len(populations))
    maximum = max(item["Total"] for item in values.values())
    fig, axis = plt.subplots(figsize=(15, 10.2))
    for component, color in COMPONENTS:
        widths = np.array([values[label][component] for label, _, _, _ in populations])
        bars = axis.barh(positions, widths, left=left, height=0.58, color=color,
                         edgecolor="#faf8f4", linewidth=2, label=component)
        for index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= maximum * 0.10:
                axis.text(left[index] + width / 2, bar.get_y() + bar.get_height() / 2,
                          f"{width:.0f}ms", ha="center", va="center", color="white",
                          fontweight="bold")
        left += widths
    for position, (label, frame, _, _) in enumerate(populations):
        total = values[label]["Total"]
        axis.text(total + maximum * 0.018, position, f"{total:.0f}ms (n={len(frame)})",
                  va="center", fontweight="bold")
    axis.set_yticks(positions, [label for label, _, _, _ in populations])
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.22)
    axis.set_xlabel("mean E2E TTFT components (ms)")
    axis.set_title(f"{CASE_TITLE}: E2E TTFT breakdown\n"
                   "All, redirected, and non-redirected requests")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, ncol=3)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "seven_series_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    rows = []
    for label, frame, _, _ in populations:
        rows.append({"series": label, "request_count": len(frame), **values[label]})
    pd.DataFrame(rows).to_csv(ANALYSIS_DIR / "seven_series_breakdown.csv", index=False)


def plot_cdf(runs):
    fig, axis = plt.subplots(figsize=(14, 7.5))
    for label, frame, color, linestyle in series(runs):
        values = np.sort(ms(frame, "e2e_ttft_ns").to_numpy())
        probabilities = np.arange(1, len(values) + 1) / len(values)
        axis.step(values, probabilities, where="post", color=color,
                  linestyle=linestyle, linewidth=2.5, label=f"{label} (n={len(frame)})")
    axis.set_xscale("log")
    axis.set_ylim(0, 1.02)
    axis.set_xlabel("E2E TTFT (ms, log scale)")
    axis.set_ylabel("cumulative probability")
    subtitle = "All, redirected, and non-redirected requests"
    axis.set_title(f"{CASE_TITLE}: E2E TTFT CDF\n{subtitle}")
    axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "seven_series_ttft_cdf.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_boxplot(runs, metric, filename, xlabel, title):
    populations = series(runs)
    if metric == "e2e":
        data = [ms(frame, "e2e_ttft_ns").to_numpy() for _, frame, _, _ in populations]
    elif metric == "router":
        data = [router_queue_ms(frame).to_numpy() for _, frame, _, _ in populations]
    elif metric == "scheduler":
        data = [ms(frame, "queueing_before_ttft_ns").to_numpy()
                for _, frame, _, _ in populations]
    else:
        data = [ms(frame, "prefill_service_ns").to_numpy()
                for _, frame, _, _ in populations]
    labels = [f"{label} (n={len(frame)})" for label, frame, _, _ in populations]
    fig, axis = plt.subplots(figsize=(14, 10.2))
    boxes = axis.boxplot(
        data, vert=False, tick_labels=labels, patch_artist=True, showmeans=True,
        widths=0.58, whis=1.5,
        meanprops={"marker": "D", "markerfacecolor": "white",
                   "markeredgecolor": "#262421", "markersize": 5},
    )
    for patch, (_, _, color, _) in zip(boxes["boxes"], populations):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)
    axis.invert_yaxis()
    axis.set_xlabel(xlabel)
    axis.set_title(title)
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / filename, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_paired_delta(runs):
    a = ms(runs["NEAREST_KV"], "e2e_ttft_ns")
    b = ms(runs["NEAREST_MIGRATE"], "e2e_ttft_ns")
    c = ms(runs["NEAREST_MIGRATE_KV"], "e2e_ttft_ns")
    fig, axis = plt.subplots(figsize=(13.5, 7.0))
    for label, values, color, linestyle in (
        ("B - A", b - a, "#d18120", "--"),
        ("C - A", c - a, "#31866f", ":"),
    ):
        ordered = np.sort(values.to_numpy())
        probability = np.arange(1, len(ordered) + 1) / len(ordered)
        axis.step(ordered, probability, where="post", linewidth=2.6,
                  color=color, linestyle=linestyle, label=label)
    axis.axvline(0, color="#333333", linestyle="--", linewidth=1.2)
    axis.set_xlabel("paired E2E TTFT delta (ms); negative means B/C is faster")
    axis.set_ylabel("cumulative probability")
    axis.set_title(f"{CASE_TITLE}: request-paired E2E TTFT delta")
    axis.grid(True, color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "paired_ttft_delta_cdf.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_arrival_bins(runs):
    baseline = runs["NEAREST_KV"]
    start = baseline.request_send_time_ns.min()
    bins = np.arange(0, 201, 20)
    labels = [f"{left}-{left + 20}" for left in bins[:-1]]
    fig, axis = plt.subplots(figsize=(13.5, 7.0))
    for policy_label, name, color, linestyle in POLICIES:
        frame = runs[name].copy()
        frame["send_s"] = (frame.request_send_time_ns - start) / 1e9
        frame["bin"] = pd.cut(frame.send_s, bins=bins, labels=labels,
                              include_lowest=True)
        means = ms(frame, "e2e_ttft_ns").groupby(frame.bin, observed=True).mean()
        axis.plot(labels, means.reindex(labels), marker="o", color=color,
                  linestyle=linestyle, linewidth=2.4, label=policy_label)
    axis.set_xlabel("request send-time bin (s)")
    axis.set_ylabel("mean E2E TTFT (ms)")
    axis.set_title(f"{CASE_TITLE}: E2E TTFT by arrival time")
    axis.grid(True, color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "arrival_bin_ttft.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def write_routing_flows(runs):
    rows = []
    for _, name, _, _ in POLICIES:
        counts = runs[name].groupby(["nearest_gpu_id", "gpu_id"]).size()
        for (home, target), count in counts.items():
            rows.append({
                "policy": name,
                "home_gpu": int(home),
                "target_gpu": int(target),
                "redirected": int(home != target),
                "request_count": int(count),
            })
    flows = pd.DataFrame(rows).sort_values(
        ["policy", "home_gpu", "redirected", "target_gpu"]
    )
    flows.to_csv(ANALYSIS_DIR / "routing_flows.csv", index=False)

    lines = [
        f"# {CASE_TITLE} routing flows",
        "",
        "| Policy | Home GPU | Target GPU | Redirected | Requests |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in flows.itertuples():
        lines.append(
            f"| {row.policy} | {row.home_gpu} | {row.target_gpu} | "
            f"{'yes' if row.redirected else 'no'} | {row.request_count} |"
        )
    (ANALYSIS_DIR / "routing_flows.md").write_text("\n".join(lines) + "\n")


def write_summary(runs):
    rows = []
    for _, name, _, _ in POLICIES:
        frame = runs[name]
        rows.append({
            "policy": name,
            "requests": len(frame),
            "redirects": int(frame.rerouted.sum()),
            "e2e_ttft_mean_ms": ms(frame, "e2e_ttft_ns").mean(),
            "e2e_ttft_p50_ms": ms(frame, "e2e_ttft_ns").quantile(0.50),
            "e2e_ttft_p95_ms": ms(frame, "e2e_ttft_ns").quantile(0.95),
            "e2e_ttft_p99_ms": ms(frame, "e2e_ttft_ns").quantile(0.99),
            "e2e_ttft_max_ms": ms(frame, "e2e_ttft_ns").max(),
            "router_queue_mean_ms": router_queue_ms(frame).mean(),
            "scheduler_queue_mean_ms": ms(frame, "queueing_before_ttft_ns").mean(),
            "prefill_mean_ms": ms(frame, "prefill_service_ns").mean(),
            "kv_transfer_mean_ms": ms(frame, "kv_migration_latency_ns").mean(),
            "completion_mean_ms": ms(frame, "request_completion_latency_ns").mean(),
            "completion_p95_ms": ms(frame, "request_completion_latency_ns").quantile(0.95),
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(ANALYSIS_DIR / "summary.csv", index=False)
    with (ANALYSIS_DIR / "summary.json").open("w") as file:
        json.dump(summary.to_dict(orient="records"), file, indent=2)


def main():
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    runs = load_runs()
    validate(runs)
    write_summary(runs)
    write_routing_flows(runs)
    plot_breakdown(runs)
    plot_cdf(runs)
    plot_boxplot(runs, "e2e", "seven_series_ttft_boxplot.png", "E2E TTFT (ms)",
                 f"{CASE_TITLE}: E2E TTFT distribution")
    plot_boxplot(runs, "router", "seven_series_router_queue_boxplot.png",
                 "router queue (ms)",
                 f"{CASE_TITLE}: router queue distribution")
    plot_boxplot(runs, "scheduler", "seven_series_scheduler_queue_boxplot.png",
                 "scheduler queue (ms)",
                 f"{CASE_TITLE}: scheduler queue distribution")
    plot_boxplot(runs, "prefill", "seven_series_prefill_boxplot.png",
                 "compute / prefill (ms)",
                 f"{CASE_TITLE}: prefill distribution")
    plot_paired_delta(runs)
    plot_arrival_bins(runs)


if __name__ == "__main__":
    main()
