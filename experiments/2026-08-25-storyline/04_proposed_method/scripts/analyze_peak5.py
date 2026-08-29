#!/usr/bin/env python3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
STORYLINE_ROOT = EXP_ROOT.parent
ANALYSIS_ROOT = EXP_ROOT / "analysis"
FIGURE_ROOT = EXP_ROOT / "figures"

RUNS = (
    ("local_only", "Local only", STORYLINE_ROOT / "03_geographical_offload_merit/results/peak_5x_local_only_ai70_30/requests.csv"),
    ("redirect_cold", "Naive cold redirect", STORYLINE_ROOT / "03_geographical_offload_merit/results/peak_5x_redirect_cold_ai70_30/requests.csv"),
    ("kv_redirect", "KV redirect", EXP_ROOT / "results/peak_5x_kv_redirect_only_ai70_30/requests.csv"),
    ("pp2", "PP=2", EXP_ROOT / "results/peak_5x_pp_only_ai70_30/requests.csv"),
    ("proposed", "KV redirect + PP=2", EXP_ROOT / "results/peak_5x_kv_redirect_pp_ai70_30/requests.csv"),
)
GROUPS = (
    (
        "01_redirect_necessity",
        "Why geographical redirect is needed",
        ("local_only", "redirect_cold"),
    ),
    (
        "02_kv_migration_necessity",
        "Why redirect should include KV cache migration",
        ("local_only", "redirect_cold", "kv_redirect"),
    ),
    (
        "03_pp2_capacity_effect",
        "Effect of PP=2 on KV cache capacity",
        ("local_only", "pp2"),
    ),
    (
        "04_proposed_ablation",
        "Proposed-method ablation",
        ("local_only", "redirect_cold", "kv_redirect", "proposed"),
    ),
)
METHOD_COLORS = {
    "local_only": "#2f5f9f",
    "redirect_cold": "#8a8178",
    "kv_redirect": "#e79b37",
    "pp2": "#8a5ca8",
    "proposed": "#31866f",
}
LINE_STYLES = {
    "local_only": "--",
    "redirect_cold": ":",
    "kv_redirect": "-.",
    "pp2": (0, (5, 2)),
    "proposed": "-",
}
COMPONENTS = (
    ("Router capacity wait", "router_capacity_wait_ns", "#c74b3a"),
    ("Scheduler queue", "scheduler_queue_exclusive_ns", "#e79b37"),
    ("Compute / prefill", "prefill_service_ns", "#31866f"),
    ("KV cache migration", "kv_migration_latency_ns", "#4f83c2"),
    ("RTT / other comm", "communication_excluding_kv_ns", "#75a8d7"),
    ("Residual", "residual_ttft_ns", "#8a8178"),
)
BACKGROUND = "#ffffff"
GRID = "#d9d2c8"
TEXT = "#262421"
CONDITION_LABEL = "APN, Active TCP 20%, Tokyo/Kagoshima AI demand 70/30"


def load_runs():
    runs = {}
    for method, label, path in RUNS:
        requests = pd.read_csv(path)
        numeric = [
            "router_capacity_wait_ns", "queueing_before_ttft_ns",
            "prefill_service_ns", "communication_latency_ns", "e2e_ttft_ns",
            "rerouted", "kv_migration_latency_ns",
        ]
        for column in numeric:
            requests[column] = pd.to_numeric(
                requests[column], errors="coerce"
            ).fillna(0.0)
        requests["scheduler_queue_exclusive_ns"] = (
            requests["queueing_before_ttft_ns"]
            - requests["router_capacity_wait_ns"]
        ).clip(lower=0.0)
        requests["communication_excluding_kv_ns"] = (
            requests["communication_latency_ns"]
            - requests["kv_migration_latency_ns"]
        ).clip(lower=0.0)
        accounted = sum(requests[column] for _, column, _ in COMPONENTS[:-1])
        requests["residual_ttft_ns"] = (
            requests["e2e_ttft_ns"] - accounted
        ).clip(lower=0.0)
        runs[method] = (label, requests)
    return runs


def write_summary(runs):
    rows = []
    for method, (label, requests) in runs.items():
        ttft = requests["e2e_ttft_ns"] / 1e6
        rerouted = requests["rerouted"] > 0
        rows.append({
            "method": method,
            "label": label,
            "requests": len(requests),
            "mean_ttft_ms": ttft.mean(),
            "p50_ttft_ms": ttft.quantile(0.50),
            "p95_ttft_ms": ttft.quantile(0.95),
            "p99_ttft_ms": ttft.quantile(0.99),
            "ttft_slo_2s_percent": 100.0 * (ttft <= 2000.0).mean(),
            "rerouted_requests": int(rerouted.sum()),
            "rerouted_percent": 100.0 * rerouted.mean(),
            "mean_queueing_ms": requests["queueing_before_ttft_ns"].mean() / 1e6,
            "p95_queueing_ms": requests["queueing_before_ttft_ns"].quantile(0.95) / 1e6,
            "mean_prefill_ms": requests["prefill_service_ns"].mean() / 1e6,
            "mean_kv_migration_ms_redirected": (
                requests.loc[rerouted, "kv_migration_latency_ns"].mean() / 1e6
                if rerouted.any() else 0.0
            ),
            "mean_kv_migration_ms_all_requests": (
                requests["kv_migration_latency_ns"].mean() / 1e6
            ),
            "mean_rtt_other_communication_ms": (
                requests["communication_excluding_kv_ns"].mean() / 1e6
            ),
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(ANALYSIS_ROOT / "peak_5x_summary.csv", index=False)


def plot_breakdown(runs, methods, output_stem, subtitle):
    values = [
        [runs[method][1][column].mean() / 1e6 for _, column, _ in COMPONENTS]
        for method in methods
    ]
    totals = np.sum(values, axis=1)
    maximum = float(np.max(totals))
    positions = np.arange(len(methods))
    left = np.zeros(len(methods))
    fig, axis = plt.subplots(figsize=(13, 7.0), facecolor=BACKGROUND)
    for index, (label, _, color) in enumerate(COMPONENTS):
        widths = [row[index] for row in values]
        bars = axis.barh(
            positions, widths, left=left, height=0.58, color=color,
            edgecolor=BACKGROUND, linewidth=2, label=label,
        )
        for row_index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= maximum * 0.065:
                axis.text(
                    left[row_index] + width / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{width:.1f}ms", ha="center", va="center", color="white",
                    fontsize=10, fontweight="bold",
                )
        left += widths
    for position, total in zip(positions, totals):
        axis.text(
            total + maximum * 0.015, position, f"{total:.1f}ms (n=600)",
            ha="left", va="center", fontsize=10.5, fontweight="bold", color=TEXT,
        )
    axis.set_yticks(positions, [runs[name][0] for name in methods], fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.20)
    axis.set_xlabel("Mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title(
        f"Peak 5x: {subtitle}\n" + CONDITION_LABEL,
        fontsize=17, pad=18,
    )
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.10), frameon=False,
        ncol=3, fontsize=9.2,
    )
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / f"peak_5x_{output_stem}_ttft_breakdown.png", dpi=180,
        bbox_inches="tight", facecolor=BACKGROUND,
    )
    plt.close(fig)


def plot_cdf(runs, methods, output_stem, subtitle):
    fig, axis = plt.subplots(figsize=(8.4, 5.8), facecolor=BACKGROUND)
    for method in methods:
        label = runs[method][0]
        requests = runs[method][1]
        ttft = np.sort(requests["e2e_ttft_ns"].to_numpy() / 1e6)
        cdf = np.arange(1, len(ttft) + 1) / len(ttft)
        axis.plot(
            ttft, cdf, color=METHOD_COLORS[method],
            linestyle=LINE_STYLES[method], linewidth=2.5, label=label,
        )
    axis.axvline(2000, color="#c74b3a", linewidth=1.5, alpha=0.65)
    axis.text(2025, 0.05, "TTFT SLO: 2s", color="#c74b3a", fontsize=9.5)
    axis.set_xlabel("E2E TTFT (ms)", fontsize=12)
    axis.set_ylabel("CDF", fontsize=12)
    axis.set_ylim(0, 1.01)
    axis.grid(color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, fontsize=9.5)
    axis.tick_params(labelsize=11)
    axis.set_title(f"Peak 5x: {subtitle}\nE2E TTFT distribution", fontsize=17, pad=14)
    fig.tight_layout()
    fig.savefig(
        FIGURE_ROOT / f"peak_5x_{output_stem}_ttft_cdf.png", dpi=180,
        bbox_inches="tight", facecolor=BACKGROUND,
    )
    plt.close(fig)


def main():
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    runs = load_runs()
    write_summary(runs)
    for output_stem, subtitle, methods in GROUPS:
        plot_breakdown(runs, methods, output_stem, subtitle)
        plot_cdf(runs, methods, output_stem, subtitle)


if __name__ == "__main__":
    main()
