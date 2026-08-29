#!/usr/bin/env python3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
RESULT_ROOT = EXP_ROOT / "results"
ANALYSIS_ROOT = EXP_ROOT / "analysis"
FIGURE_ROOT = EXP_ROOT / "figures"

METHODS = ("local_only", "redirect_cold")
LABELS = {
    "local_only": "Local only",
    "redirect_cold": "Redirect to available GPU",
}
METHOD_COLORS = {"local_only": "#2f5f9f", "redirect_cold": "#31866f"}
LINE_STYLES = {"local_only": "--", "redirect_cold": "-"}
COMPONENTS = (
    ("Router capacity wait", "router_capacity_wait_ns", "#c74b3a"),
    ("Scheduler queue", "scheduler_queue_exclusive_ns", "#e79b37"),
    ("Compute / prefill", "prefill_service_ns", "#31866f"),
    ("RTT / other comm", "communication_latency_ns", "#4f83c2"),
    ("Residual", "residual_ttft_ns", "#8a8178"),
)
BACKGROUND = "#ffffff"
GRID = "#d9d2c8"
TEXT = "#262421"


def load_run(method, peak):
    directory = RESULT_ROOT / f"peak_{peak}x_{method}_ai70_30"
    requests = pd.read_csv(directory / "requests.csv")
    numeric = [
        "router_capacity_wait_ns", "queueing_before_ttft_ns",
        "prefill_service_ns", "communication_latency_ns", "e2e_ttft_ns",
        "rerouted",
    ]
    for column in numeric:
        requests[column] = pd.to_numeric(requests[column], errors="coerce").fillna(0.0)
    requests["scheduler_queue_exclusive_ns"] = (
        requests["queueing_before_ttft_ns"] - requests["router_capacity_wait_ns"]
    ).clip(lower=0.0)
    accounted = sum(requests[column] for _, column, _ in COMPONENTS[:-1])
    requests["residual_ttft_ns"] = (
        requests["e2e_ttft_ns"] - accounted
    ).clip(lower=0.0)
    return requests


def summarize(method, peak, requests):
    ttft = requests["e2e_ttft_ns"] / 1e6
    initial_gpu = pd.to_numeric(
        requests["router_initial_instance_id"], errors="coerce"
    ).fillna(-1).astype(int)
    decision_gpu = pd.to_numeric(
        requests["router_decision_instance_id"], errors="coerce"
    ).fillna(-1).astype(int)
    rerouted = requests["rerouted"] > 0
    cross_site = rerouted & ((initial_gpu < 4) != (decision_gpu < 4))
    tokyo_to_kagoshima = rerouted & (initial_gpu < 4) & (decision_gpu >= 4)
    row = {
        "method": method,
        "peak_nx": peak,
        "requests": len(requests),
        "mean_ttft_ms": ttft.mean(),
        "p50_ttft_ms": ttft.quantile(0.50),
        "p95_ttft_ms": ttft.quantile(0.95),
        "p99_ttft_ms": ttft.quantile(0.99),
        "rerouted_requests": int(requests["rerouted"].sum()),
        "cross_site_redirects": int(cross_site.sum()),
        "tokyo_to_kagoshima_redirects": int(tokyo_to_kagoshima.sum()),
        "mean_router_capacity_wait_ms": requests["router_capacity_wait_ns"].mean() / 1e6,
        "p95_router_capacity_wait_ms": requests["router_capacity_wait_ns"].quantile(0.95) / 1e6,
    }
    for label, column, _ in COMPONENTS:
        key = label.lower().replace(" / ", "_").replace(" ", "_")
        row[f"mean_{key}_ms"] = requests[column].mean() / 1e6
    return row


def plot_breakdown(peak, runs):
    values = [
        [runs[method][column].mean() / 1e6 for _, column, _ in COMPONENTS]
        for method in METHODS
    ]
    totals = np.sum(values, axis=1)
    maximum = float(np.max(totals))
    positions = np.arange(len(METHODS))
    left = np.zeros(len(METHODS))
    fig, axis = plt.subplots(figsize=(13, 5.5), facecolor=BACKGROUND)
    for index, (label, _, color) in enumerate(COMPONENTS):
        widths = [row[index] for row in values]
        bars = axis.barh(
            positions, widths, left=left, height=0.58, color=color,
            edgecolor=BACKGROUND, linewidth=2, label=label,
        )
        for row_index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= maximum * 0.075:
                axis.text(
                    left[row_index] + width / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{width:.1f}ms", ha="center", va="center", color="white",
                    fontsize=10.5, fontweight="bold",
                )
        left += widths
    for position, total in zip(positions, totals):
        axis.text(
            total + maximum * 0.018, position, f"{total:.1f}ms (n=600)",
            ha="left", va="center", fontsize=11, fontweight="bold", color=TEXT,
        )
    axis.set_yticks(positions, [LABELS[name] for name in METHODS], fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.20)
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title(
        f"Peak {peak}x: geographical redirect TTFT breakdown\n"
        "PP=1, APN, Active TCP 20%, no KV migration",
        fontsize=17, pad=18,
    )
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False,
        ncol=5, fontsize=9.5,
    )
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / f"peak_{peak}x_ttft_breakdown.png", dpi=180,
        bbox_inches="tight", facecolor=BACKGROUND,
    )
    plt.close(fig)


def plot_cdf(peak, runs):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), facecolor=BACKGROUND)
    for method in METHODS:
        ttft = np.sort(runs[method]["e2e_ttft_ns"].to_numpy() / 1e6)
        wait = np.sort(runs[method]["router_capacity_wait_ns"].to_numpy() / 1e6)
        cdf = np.arange(1, len(ttft) + 1) / len(ttft)
        axes[0].plot(
            ttft, cdf, color=METHOD_COLORS[method],
            linestyle=LINE_STYLES[method], linewidth=2.5, label=LABELS[method],
        )
        axes[1].plot(
            wait, cdf, color=METHOD_COLORS[method],
            linestyle=LINE_STYLES[method], linewidth=2.5, label=LABELS[method],
        )
    axes[0].set_xlabel("E2E TTFT (ms)", fontsize=12)
    axes[0].set_title("E2E TTFT distribution", fontsize=16, pad=14)
    axes[1].set_xlabel("Router capacity wait (ms)", fontsize=12)
    axes[1].set_title("Capacity-wait distribution", fontsize=16, pad=14)
    for axis in axes:
        axis.set_ylabel("CDF", fontsize=12)
        axis.set_ylim(0, 1.01)
        axis.grid(color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=9.5)
        axis.tick_params(labelsize=11)
    fig.suptitle(
        f"Peak {peak}x: geographical redirect latency CDF — PP=1, APN",
        fontsize=17,
    )
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / f"peak_{peak}x_ttft_cdf.png", dpi=180,
        bbox_inches="tight", facecolor=BACKGROUND,
    )
    plt.close(fig)


def plot_sweep(summary):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), facecolor=BACKGROUND)
    for method in METHODS:
        data = summary[summary["method"] == method].sort_values("peak_nx")
        for metric, suffix, alpha in (
            ("mean_ttft_ms", "mean", 1.0),
            ("p95_ttft_ms", "p95", 0.70),
            ("p99_ttft_ms", "p99", 0.43),
        ):
            axes[0].plot(
                data["peak_nx"], data[metric], color=METHOD_COLORS[method],
                linestyle=LINE_STYLES[method], linewidth=2.4, alpha=alpha,
                marker="o", markersize=4, label=f"{LABELS[method]} {suffix}",
            )
        axes[1].plot(
            data["peak_nx"], data["mean_router_capacity_wait_ms"],
            color=METHOD_COLORS[method], linestyle=LINE_STYLES[method],
            linewidth=2.5, marker="o", label=LABELS[method],
        )
    axes[0].set_ylabel("E2E TTFT (ms)", fontsize=12)
    axes[0].set_title("TTFT across offered load", fontsize=16, pad=14)
    axes[1].set_ylabel("Mean capacity wait (ms)", fontsize=12)
    axes[1].set_title("Capacity wait across offered load", fontsize=16, pad=14)
    for axis in axes:
        axis.set_xlabel("Peak multiplier", fontsize=12)
        axis.set_xticks(range(2, 11))
        axis.grid(color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=8.5)
    fig.suptitle(
        "Geographical redirect sweep — Peak 2x--10x, PP=1, APN",
        fontsize=17,
    )
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / "peak_2x_to_10x_sweep.png", dpi=180,
        bbox_inches="tight", facecolor=BACKGROUND,
    )
    plt.close(fig)


def main():
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for peak in range(2, 11):
        runs = {}
        for method in METHODS:
            requests = load_run(method, peak)
            runs[method] = requests
            rows.append(summarize(method, peak, requests))
        plot_breakdown(peak, runs)
        plot_cdf(peak, runs)
    summary = pd.DataFrame(rows).sort_values(["peak_nx", "method"])
    summary.to_csv(ANALYSIS_ROOT / "summary.csv", index=False)
    plot_sweep(summary)


if __name__ == "__main__":
    main()
