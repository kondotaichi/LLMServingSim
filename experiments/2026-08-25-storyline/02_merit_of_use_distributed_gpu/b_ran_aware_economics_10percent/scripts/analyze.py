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

ENVIRONMENTS = ("cloud", "airan_apn", "airan_wan")
LABELS = {
    "cloud": "Cloud (Miyabi-like)",
    "airan_apn": "AI-RAN (APN)",
    "airan_wan": "AI-RAN (WAN)",
}
METHOD_COLORS = {
    "cloud": "#2f5f9f",
    "airan_apn": "#31866f",
    "airan_wan": "#d18120",
}
LINE_STYLES = {"cloud": "--", "airan_apn": "-", "airan_wan": ":"}
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
TOKYO_YEN_PER_KWH = 23.0
GH200_LOW_LOAD_W = 117.0
GH200_GPU_LIMIT_W = 900.0
MIYABI_YEN_PER_HOUR = 306.0


def load_run(environment, pp, peak):
    directory = RESULT_ROOT / f"{environment}_pp{pp}_peak_{peak}x_nearest_kv"
    requests = pd.read_csv(directory / "requests.csv")
    gpus = pd.read_csv(directory / "gpus.csv")
    numeric = [
        "router_capacity_wait_ns", "queueing_before_ttft_ns",
        "prefill_service_ns", "communication_latency_ns", "e2e_ttft_ns",
    ]
    for column in numeric:
        requests[column] = pd.to_numeric(requests[column], errors="coerce").fillna(0.0)
    requests["scheduler_queue_exclusive_ns"] = (
        requests["queueing_before_ttft_ns"] - requests["router_capacity_wait_ns"]
    ).clip(lower=0.0)
    accounted = sum(requests[column] for _, column, _ in COMPONENTS[:-1])
    requests["residual_ttft_ns"] = (requests["e2e_ttft_ns"] - accounted).clip(lower=0.0)
    return requests, gpus


def power_metrics(gpus, pp):
    physical = gpus
    if pp > 1:
        physical = pd.concat([gpus[gpus["request_count"] > 0]] * pp, ignore_index=True)
    utilization = physical["busy_time_ns"] / physical["observation_time_ns"]
    power_w = GH200_LOW_LOAD_W + (GH200_GPU_LIMIT_W - GH200_LOW_LOAD_W) * utilization
    energy_wh = (power_w * physical["observation_time_ns"] / 1e9 / 3600).sum()
    elapsed_s = (gpus["observation_end_ns"].max() - gpus["observation_start_ns"].min()) / 1e9
    return {
        "mean_gpu_utilization_pct": utilization.mean() * 100,
        "estimated_cluster_power_w": power_w.sum(),
        "estimated_electricity_yen_per_hour": power_w.sum() / 1000 * TOKYO_YEN_PER_KWH,
        "estimated_electricity_yen_per_1000_requests": energy_wh / 1000 * TOKYO_YEN_PER_KWH / 600 * 1000,
        "miyabi_yen_per_1000_requests": MIYABI_YEN_PER_HOUR * elapsed_s / 3600 / 600 * 1000,
        "elapsed_s": elapsed_s,
    }


def summarize(environment, pp, peak, requests, gpus):
    ttft = requests["e2e_ttft_ns"] / 1e6
    router_wait = requests["router_capacity_wait_ns"] / 1e6
    retries = pd.to_numeric(requests["router_capacity_retry_count"], errors="coerce").fillna(0)
    available_kv = pd.to_numeric(requests["router_initial_available_kv_bytes"], errors="coerce") / 1e9
    row = {
        "environment": environment,
        "pp": pp,
        "peak_nx": peak,
        "requests": len(requests),
        "mean_ttft_ms": ttft.mean(),
        "p50_ttft_ms": ttft.quantile(0.50),
        "p95_ttft_ms": ttft.quantile(0.95),
        "p99_ttft_ms": ttft.quantile(0.99),
        "mean_router_capacity_wait_ms": router_wait.mean(),
        "p95_router_capacity_wait_ms": router_wait.quantile(0.95),
        "max_router_capacity_wait_ms": router_wait.max(),
        "initially_inadmissible_requests": int((retries > 0).sum()),
        "capacity_retry_count": int(retries.sum()),
        "max_capacity_retries_per_request": int(retries.max()),
        "min_available_kv_gb": available_kv.min(),
    }
    for label, column, _ in COMPONENTS:
        row[f"mean_{label.lower().replace(' / ', '_').replace(' ', '_')}_ms"] = requests[column].mean() / 1e6
    row.update(power_metrics(gpus, pp))
    return row


def plot_breakdown(pp, peak, runs):
    values = []
    for environment in ENVIRONMENTS:
        requests = runs[environment]
        values.append([requests[column].mean() / 1e6 for _, column, _ in COMPONENTS])
    totals = np.sum(values, axis=1)
    maximum = float(np.max(totals))
    positions = np.arange(len(ENVIRONMENTS))
    left = np.zeros(len(ENVIRONMENTS))
    fig, axis = plt.subplots(figsize=(13, 6.2), facecolor=BACKGROUND)
    for index, (label, _, color) in enumerate(COMPONENTS):
        widths = [row[index] for row in values]
        bars = axis.barh(positions, widths, left=left, height=0.58, color=color,
                         edgecolor=BACKGROUND, linewidth=2, label=label)
        for row_index, (bar, width) in enumerate(zip(bars, widths)):
            if width >= maximum * 0.08:
                axis.text(left[row_index] + width / 2, bar.get_y() + bar.get_height() / 2,
                          f"{width:.1f}ms", ha="center", va="center", color="white",
                          fontsize=10.5, fontweight="bold")
        left += widths
    for position, total in zip(positions, totals):
        axis.text(total + maximum * 0.018, position, f"{total:.1f}ms (n=600)",
                  ha="left", va="center", fontsize=11, fontweight="bold", color=TEXT)
    axis.set_yticks(positions, [LABELS[name] for name in ENVIRONMENTS], fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.18)
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title(f"PP={pp}, Peak {peak}x: RAN-aware E2E TTFT breakdown\nNEAREST_KV routing",
                   fontsize=17, pad=18)
    axis.grid(axis="x", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), frameon=False,
                ncol=5, fontsize=9.5)
    fig.tight_layout()
    FIGURE_ROOT.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_ROOT / f"pp{pp}_peak_{peak}x_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor=BACKGROUND)
    plt.close(fig)


def plot_cdf(pp, peak, runs):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), facecolor=BACKGROUND)
    for environment in ENVIRONMENTS:
        values = np.sort(runs[environment]["e2e_ttft_ns"].to_numpy() / 1e6)
        cdf = np.arange(1, len(values) + 1) / len(values)
        axes[0].plot(values, cdf, color=METHOD_COLORS[environment],
                     linestyle=LINE_STYLES[environment], linewidth=2.5,
                     label=LABELS[environment])
    for environment in ("airan_apn", "airan_wan"):
        values = np.sort(runs[environment]["router_capacity_wait_ns"].to_numpy() / 1e6)
        cdf = np.arange(1, len(values) + 1) / len(values)
        axes[1].plot(values, cdf, color=METHOD_COLORS[environment],
                     linestyle=LINE_STYLES[environment], linewidth=2.5,
                     label=LABELS[environment])
    axes[0].set_xlabel("E2E TTFT (ms)", fontsize=12)
    axes[0].set_title("E2E TTFT distribution", fontsize=16, pad=14)
    axes[1].set_xlabel("Router capacity wait (ms)", fontsize=12)
    axes[1].set_title("AI-RAN capacity-wait distribution", fontsize=16, pad=14)
    for axis in axes:
        axis.set_ylabel("CDF", fontsize=12)
        axis.set_ylim(0, 1.01)
        axis.grid(color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=9.5)
        axis.tick_params(labelsize=11)
    fig.suptitle(f"PP={pp}, Peak {peak}x: RAN-aware latency CDF — NEAREST_KV", fontsize=17)
    fig.tight_layout()
    fig.savefig(FIGURE_ROOT / f"pp{pp}_peak_{peak}x_ttft_cdf.png", dpi=180,
                bbox_inches="tight", facecolor=BACKGROUND)
    plt.close(fig)


def main():
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for peak in (1, 10):
        for pp in (1, 2):
            runs = {}
            for environment in ENVIRONMENTS:
                requests, gpus = load_run(environment, pp, peak)
                runs[environment] = requests
                rows.append(summarize(environment, pp, peak, requests, gpus))
            plot_breakdown(pp, peak, runs)
            plot_cdf(pp, peak, runs)
    pd.DataFrame(rows).to_csv(ANALYSIS_ROOT / "summary.csv", index=False)


if __name__ == "__main__":
    main()
