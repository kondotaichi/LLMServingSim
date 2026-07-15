#!/usr/bin/env python3
"""Analyze the 60-second input-512, zero-prefix-reuse experiment."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = EXPERIMENT_DIR / "results/input512_reuse00"
FIGURE_DIR = EXPERIMENT_DIR / "figures/input512_reuse00"
ANALYSIS_DIR = EXPERIMENT_DIR / "analysis/input512_reuse00"
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
        name: pd.read_csv(RESULT_DIR / name / "requests.csv").sort_values("request id")
        for _, name, _, _ in POLICIES
    }


def ms(frame, column):
    return frame[column] / 1e6


def router_queue_ms(frame):
    values = (
        frame["e2e_ttft_ns"]
        - frame["prefill_service_ns"]
        - frame["communication_latency_ns"]
        - frame["queueing_before_ttft_ns"]
    ) / 1e6
    return values.clip(lower=0.0)


def breakdown(frame):
    communication = ms(frame, "communication_latency_ns").mean()
    transfer = ms(frame, "kv_migration_latency_ns").mean()
    return {
        "Router queue": router_queue_ms(frame).mean(),
        "Scheduler queue": ms(frame, "queueing_before_ttft_ns").mean(),
        "KV transfer": transfer,
        "Compute / prefill": ms(frame, "prefill_service_ns").mean(),
        "RTT / other comm": max(0.0, communication - transfer),
    }


def validate(runs):
    baseline = runs["NEAREST_KV"].reset_index(drop=True)
    columns = [
        "request id", "input", "output", "reuse_prefix_toks", "gpu_id", "rerouted",
        "e2e_ttft_ns", "queueing_before_ttft_ns", "prefill_service_ns",
        "request_completion_latency_ns",
    ]
    for name, frame in runs.items():
        candidate = frame.reset_index(drop=True)
        for column in columns:
            if not baseline[column].equals(candidate[column]):
                raise ValueError(f"Policy results differ in {column}: {name}")


def plot_breakdown(runs):
    labels = [label for label, _, _, _ in POLICIES]
    data = {name: breakdown(runs[name]) for _, name, _, _ in POLICIES}
    positions = np.arange(len(POLICIES))
    left = np.zeros(len(POLICIES))
    fig, axis = plt.subplots(figsize=(13.5, 6.8))
    for component, color in COMPONENTS:
        widths = np.array([data[name][component] for _, name, _, _ in POLICIES])
        bars = axis.barh(positions, widths, left=left, height=0.58, color=color,
                         edgecolor="#faf8f4", linewidth=2, label=component)
        for index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= 3:
                axis.text(left[index] + width / 2, bar.get_y() + bar.get_height() / 2,
                          f"{width:.1f}ms", ha="center", va="center", color="white",
                          fontweight="bold")
        left += widths
    for position, total in zip(positions, left):
        axis.text(total + 1.2, position, f"{total:.1f}ms (n=300)", va="center",
                  fontweight="bold")
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.set_xlim(0, max(left) * 1.28)
    axis.set_xlabel("mean E2E TTFT components (ms)")
    axis.set_title("Input 512 / reuse 0% / 60s: E2E TTFT breakdown\nNo redirects occurred")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", frameon=False, ncol=3)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "three_policy_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cdf(runs, column, filename, xlabel, title, log_scale=False):
    fig, axis = plt.subplots(figsize=(13.5, 7.0))
    for label, name, color, linestyle in POLICIES:
        values = np.sort(ms(runs[name], column).to_numpy())
        probabilities = np.arange(1, len(values) + 1) / len(values)
        axis.step(values, probabilities, where="post", color=color, linestyle=linestyle,
                  linewidth=3.0, alpha=0.9, label=f"{label} (n={len(values)})")
    if log_scale:
        axis.set_xscale("log")
    axis.set_ylim(0, 1.02)
    axis.set_xlabel(xlabel)
    axis.set_ylabel("cumulative probability")
    axis.set_title(f"{title}\nCurves overlap exactly across all three policies")
    axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / filename, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_boxplot(runs, values_fn, filename, xlabel, title):
    data = [values_fn(runs[name]).to_numpy() for _, name, _, _ in POLICIES]
    labels = [label for label, _, _, _ in POLICIES]
    colors = [color for _, _, color, _ in POLICIES]
    fig, axis = plt.subplots(figsize=(12.5, 6.8))
    boxes = axis.boxplot(data, vert=False, tick_labels=labels, patch_artist=True,
                         showmeans=True, widths=0.55,
                         meanprops={"marker": "D", "markerfacecolor": "white",
                                    "markeredgecolor": "#262421", "markersize": 5})
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
    axis.invert_yaxis()
    axis.set_xlabel(xlabel)
    axis.set_title(f"{title}\nDistributions are identical across all three policies")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / filename, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_gpu_summary():
    frame = pd.read_csv(RESULT_DIR / "NEAREST_KV/gpus.csv")
    fig, axis = plt.subplots(figsize=(13.5, 6.8))
    width = 0.38
    gpu_ids = frame["gpu_id"].to_numpy()
    axis.bar(gpu_ids - width / 2, frame["mean_e2e_ttft_ms"], width=width,
             color="#2f5f9f", label="mean E2E TTFT")
    axis.bar(gpu_ids + width / 2, frame["mean_queueing_before_ttft_ms"], width=width,
             color="#e79b37", label="mean scheduler queue")
    axis.set_xticks(gpu_ids)
    axis.set_xlabel("home GPU / cell ID")
    axis.set_ylabel("mean latency (ms)")
    axis.set_title("Input 512 / reuse 0% / 60s: latency by home GPU\nAll requests remained on their home GPU")
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "gpu_ttft_and_queue.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def write_summary(runs):
    rows = []
    for _, name, _, _ in POLICIES:
        frame = runs[name]
        components = breakdown(frame)
        rows.append({
            "policy": name,
            "requests": len(frame),
            "redirects": int(frame["rerouted"].sum()),
            "e2e_ttft_mean_ms": ms(frame, "e2e_ttft_ns").mean(),
            "e2e_ttft_p50_ms": ms(frame, "e2e_ttft_ns").quantile(0.50),
            "e2e_ttft_p95_ms": ms(frame, "e2e_ttft_ns").quantile(0.95),
            "e2e_ttft_p99_ms": ms(frame, "e2e_ttft_ns").quantile(0.99),
            "e2e_ttft_max_ms": ms(frame, "e2e_ttft_ns").max(),
            "router_queue_mean_ms": components["Router queue"],
            "scheduler_queue_mean_ms": components["Scheduler queue"],
            "prefill_mean_ms": components["Compute / prefill"],
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
    plot_breakdown(runs)
    plot_cdf(runs, "e2e_ttft_ns", "three_policy_ttft_cdf.png", "E2E TTFT (ms)",
             "Input 512 / reuse 0% / 60s: E2E TTFT CDF")
    plot_cdf(runs, "request_completion_latency_ns", "three_policy_completion_cdf.png",
             "request completion latency (ms, log scale)",
             "Input 512 / reuse 0% / 60s: completion-latency CDF", log_scale=True)
    plot_boxplot(runs, lambda frame: ms(frame, "e2e_ttft_ns"),
                 "three_policy_ttft_boxplot.png", "E2E TTFT (ms)",
                 "Input 512 / reuse 0% / 60s: E2E TTFT distribution")
    plot_boxplot(runs, lambda frame: ms(frame, "queueing_before_ttft_ns"),
                 "three_policy_scheduler_queue_boxplot.png", "scheduler queue (ms)",
                 "Input 512 / reuse 0% / 60s: scheduler queue distribution")
    plot_gpu_summary()


if __name__ == "__main__":
    main()
