#!/usr/bin/env python3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
STORY_ROOT = EXP_ROOT.parents[1]
REPO_ROOT = STORY_ROOT.parents[1]
PRIOR_ROOT = REPO_ROOT / "experiments/2026-08-24-simulate-both-local-and-cloudlike"
FIGURE_ROOT = EXP_ROOT / "figures"
ANALYSIS_ROOT = EXP_ROOT / "analysis"

COLORS = {"cloud": "#2f5f9f", "distributed": "#31866f"}
LABELS = {"cloud": "Cloud (Miyabi-like)", "distributed": "Distributed (APN)"}
COMPONENTS = ["Router queue", "Scheduler queue", "Compute / prefill", "RTT / other comm", "Residual"]
COMPONENT_COLORS = ["#c74b3a", "#e79b37", "#31866f", "#4f83c2", "#8a8178"]
TEXT_COLOR = "#262421"
GRID_COLOR = "#d9d2c8"
BACKGROUND_COLOR = "#ffffff"
TOKYO_YEN_PER_KWH = 23.0
GH200_LOW_LOAD_W = 117.0
GH200_GPU_LIMIT_W = 900.0
MIYABI_8_NODE_YEN_PER_HOUR = 306.0


def completed(path):
    if not path.exists():
        return False
    with path.open("rb") as stream:
        return sum(1 for _ in stream) - 1 == 600


def source_path(pp, environment, peak):
    local = EXP_ROOT / "results" / f"pp{pp}_{environment}_peak_{peak}x_nearest_kv" / "requests.csv"
    if completed(local):
        return local

    if pp == 1:
        prior_env = "clustered_cloud" if environment == "cloud" else "distributed_apn"
        prior_base = "results" if environment == "cloud" else "results_no_ran_no_pp"
        run = f"peak_{peak}x_repeat600_seed1_1_nearest_kv"
    else:
        prior_env = "clustered_cloud" if environment == "cloud" else "distributed_apn"
        prior_base = "results_no_ran_pp_exist"
        run = f"peak_{peak}x_repeat600_seed1_pp2_1_nearest_kv"
    prior = PRIOR_ROOT / prior_base / prior_env / run / "requests.csv"
    if completed(prior):
        return prior
    raise FileNotFoundError(f"missing complete result: pp={pp}, env={environment}, peak={peak}x")


def load_run(pp, environment, peak):
    path = source_path(pp, environment, peak)
    frame = pd.read_csv(path)
    numeric = [
        "e2e_ttft_ns", "communication_latency_ns", "router_capacity_wait_ns",
        "queueing_before_ttft_ns", "prefill_service_ns",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    # The legacy PP=2 workloads were generated with every access-network
    # latency field set to zero.  The PP=1 and PP=2 sweeps otherwise use the
    # same 600 requests, so restore each request's access RTT from the matching
    # PP=1 result.  PP communication inside model execution remains accounted
    # for by the simulator; this only restores the user-to-site RTT omitted by
    # the legacy workload preparation step.
    if pp > 1 and not frame["communication_latency_ns"].any():
        pp1_path = source_path(1, environment, peak)
        pp1_frame = pd.read_csv(
            pp1_path, usecols=["request id", "communication_latency_ns"]
        )
        pp1_frame["communication_latency_ns"] = pd.to_numeric(
            pp1_frame["communication_latency_ns"], errors="coerce"
        ).fillna(0.0)
        restored = frame[["request id"]].merge(
            pp1_frame,
            on="request id",
            how="left",
            validate="one_to_one",
        )["communication_latency_ns"]
        if restored.isna().any():
            raise ValueError(
                f"cannot restore PP={pp} access RTT from {pp1_path}: "
                "request IDs do not match"
            )
        frame["communication_latency_ns"] = restored.to_numpy()
        frame["e2e_ttft_ns"] += frame["communication_latency_ns"]

    accounted = (
        frame["communication_latency_ns"]
        + frame["router_capacity_wait_ns"]
        + frame["queueing_before_ttft_ns"]
        + frame["prefill_service_ns"]
    )
    frame["residual_ttft_ns"] = (frame["e2e_ttft_ns"] - accounted).clip(lower=0.0)
    return frame, path


def percentile(series, quantile):
    return float(series.quantile(quantile) / 1e6)


def summarize(pp, environment, peak, frame, path):
    gpu_frame = pd.read_csv(path.parent / "gpus.csv")
    if pp > 1:
        # Current PP accounting records busy time only on each instance's lead GPU.
        # Project the lead-GPU utilization to every physical pipeline stage.
        gpu_frame = pd.concat(
            [gpu_frame[gpu_frame["request_count"] > 0]] * pp,
            ignore_index=True,
        )
    utilization = gpu_frame["busy_time_ns"] / gpu_frame["observation_time_ns"]
    estimated_power_w = GH200_LOW_LOAD_W + (
        GH200_GPU_LIMIT_W - GH200_LOW_LOAD_W
    ) * utilization
    energy_wh = (
        estimated_power_w * gpu_frame["observation_time_ns"] / 1e9 / 3600
    ).sum()
    elapsed_s = (
        gpu_frame["observation_end_ns"].max()
        - gpu_frame["observation_start_ns"].min()
    ) / 1e9
    return {
        "pp": pp,
        "environment": environment,
        "peak_nx": peak,
        "requests": len(frame),
        "mean_ttft_ms": frame["e2e_ttft_ns"].mean() / 1e6,
        "p50_ttft_ms": percentile(frame["e2e_ttft_ns"], 0.50),
        "p95_ttft_ms": percentile(frame["e2e_ttft_ns"], 0.95),
        "p99_ttft_ms": percentile(frame["e2e_ttft_ns"], 0.99),
        "communication_ms": frame["communication_latency_ns"].mean() / 1e6,
        "router_queue_ms": frame["router_capacity_wait_ns"].mean() / 1e6,
        "scheduler_queue_ms": frame["queueing_before_ttft_ns"].mean() / 1e6,
        "prefill_ms": frame["prefill_service_ns"].mean() / 1e6,
        "residual_ms": frame["residual_ttft_ns"].mean() / 1e6,
        "mean_gpu_utilization_pct": utilization.mean() * 100,
        "estimated_cluster_power_w": estimated_power_w.sum(),
        "estimated_energy_wh": energy_wh,
        "estimated_electricity_yen": energy_wh / 1000 * TOKYO_YEN_PER_KWH,
        "estimated_electricity_yen_per_hour": estimated_power_w.sum() / 1000 * TOKYO_YEN_PER_KWH,
        "estimated_electricity_yen_per_1000_requests": (
            energy_wh / 1000 * TOKYO_YEN_PER_KWH / len(frame) * 1000
        ),
        "miyabi_yen_same_elapsed": MIYABI_8_NODE_YEN_PER_HOUR * elapsed_s / 3600,
        "miyabi_yen_per_1000_requests": (
            MIYABI_8_NODE_YEN_PER_HOUR * elapsed_s / 3600 / len(frame) * 1000
        ),
        "elapsed_s": elapsed_s,
        "source_csv": str(path.relative_to(REPO_ROOT)),
    }


def plot_breakdown(pp, peak, runs):
    output_dir = FIGURE_ROOT / f"pp{pp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    values = []
    for environment in ("cloud", "distributed"):
        frame = runs[environment]
        values.append([
            frame["router_capacity_wait_ns"].mean() / 1e6,
            frame["queueing_before_ttft_ns"].mean() / 1e6,
            frame["prefill_service_ns"].mean() / 1e6,
            frame["communication_latency_ns"].mean() / 1e6,
            frame["residual_ttft_ns"].mean() / 1e6,
        ])
    fig, axis = plt.subplots(figsize=(12.0, 5.4), facecolor=BACKGROUND_COLOR)
    axis.set_facecolor(BACKGROUND_COLOR)
    left = np.zeros(2)
    totals = np.sum(values, axis=1)
    maximum = float(np.max(totals))
    for index, component in enumerate(COMPONENTS):
        component_values = [values[0][index], values[1][index]]
        bars = axis.barh(
            [0, 1], component_values, left=left, height=0.58,
            color=COMPONENT_COLORS[index], edgecolor=BACKGROUND_COLOR,
            linewidth=2, label=component,
        )
        for row_index, (bar, width) in enumerate(zip(bars, component_values)):
            if width >= maximum * 0.08:
                axis.text(
                    left[row_index] + width / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{width:.1f}ms", ha="center", va="center",
                    color="white", fontsize=10.5, fontweight="bold",
                )
        left += component_values
    for position, total in enumerate(totals):
        axis.text(
            total + maximum * 0.018, position, f"{total:.1f}ms (n={len(runs['cloud'])})",
            ha="left", va="center", fontsize=11, fontweight="bold", color=TEXT_COLOR,
        )
    axis.set_yticks([0, 1], [LABELS["cloud"], LABELS["distributed"]], fontsize=11.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.18)
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=12)
    axis.set_title(f"PP={pp}, Peak {peak}x: E2E TTFT breakdown\nNEAREST_KV routing", fontsize=17, pad=18)
    axis.grid(axis="x", color=GRID_COLOR, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10),
                frameon=False, ncol=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(output_dir / f"peak_{peak}x_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor=BACKGROUND_COLOR)
    plt.close(fig)


def plot_cdf(pp, peak, runs):
    output_dir = FIGURE_ROOT / f"pp{pp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.4), facecolor=BACKGROUND_COLOR)
    axis = axes[0]
    for environment in ("cloud", "distributed"):
        values = np.sort(runs[environment]["e2e_ttft_ns"].to_numpy(dtype=float) / 1e6)
        cdf = np.arange(1, len(values) + 1) / len(values)
        style = "--" if environment == "cloud" else "-"
        axis.plot(values, cdf, linewidth=2.5, linestyle=style,
                  color=COLORS[environment], label=LABELS[environment])
    axis.set_xlabel("E2E TTFT (ms)", fontsize=12)
    axis.set_ylabel("CDF", fontsize=12)
    axis.set_title("E2E TTFT distribution", fontsize=16, pad=14)
    axis.set_ylim(0.0, 1.01)
    axis.grid(color=GRID_COLOR, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, fontsize=9.5)

    cloud = runs["cloud"].sort_values("request id")
    distributed = runs["distributed"].sort_values("request id")
    paired = cloud[["request id", "e2e_ttft_ns"]].merge(
        distributed[["request id", "e2e_ttft_ns"]],
        on="request id", suffixes=("_cloud", "_distributed"), validate="one_to_one",
    )
    scale = 1e3 if pp == 1 else 1e6
    unit = "us" if pp == 1 else "ms"
    delta = np.sort(
        (paired["e2e_ttft_ns_distributed"] - paired["e2e_ttft_ns_cloud"]).to_numpy()
        / scale
    )
    delta_cdf = np.arange(1, len(delta) + 1) / len(delta)
    axes[1].plot(delta, delta_cdf, linewidth=2.5, color="#c74b3a")
    axes[1].axvline(0, color="black", linewidth=1.0, linestyle=":")
    axes[1].set_xlabel(f"Paired TTFT delta: Distributed - Cloud ({unit})", fontsize=12)
    axes[1].set_ylabel("CDF", fontsize=12)
    axes[1].set_title("Same-request delta (magnified)", fontsize=16, pad=14)
    axes[1].set_ylim(0.0, 1.01)
    axes[1].grid(color=GRID_COLOR, linewidth=0.8)
    axes[1].set_axisbelow(True)
    axes[1].spines[["top", "right"]].set_visible(False)
    for panel in axes:
        panel.tick_params(labelsize=11)
        panel.set_facecolor(BACKGROUND_COLOR)
    fig.suptitle(f"PP={pp}, Peak {peak}x: TTFT CDF — NEAREST_KV", fontsize=17)
    fig.tight_layout()
    fig.savefig(output_dir / f"peak_{peak}x_ttft_cdf.png", dpi=180,
                bbox_inches="tight", facecolor=BACKGROUND_COLOR)
    plt.close(fig)


def plot_sweep(summary):
    for pp in (1, 2):
        subset = summary[summary["pp"] == pp]
        fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.6))
        for environment in ("cloud", "distributed"):
            data = subset[subset["environment"] == environment].sort_values("peak_nx")
            axes[0].plot(data["peak_nx"], data["mean_ttft_ms"], marker="o",
                         color=COLORS[environment], label=LABELS[environment])
            axes[1].plot(data["peak_nx"], data["p95_ttft_ms"], marker="o",
                         color=COLORS[environment], label=LABELS[environment])
        axes[0].set_title("Mean TTFT")
        axes[1].set_title("p95 TTFT")
        paired = subset.pivot(index="peak_nx", columns="environment", values="mean_ttft_ms")
        scale = 1000 if pp == 1 else 1
        unit = "us" if pp == 1 else "ms"
        axes[2].plot(paired.index, (paired["distributed"] - paired["cloud"]) * scale,
                     marker="o", color="#E45756", label="Distributed - Cloud")
        axes[2].axhline(0, color="black", linewidth=1.0, linestyle=":")
        axes[2].set_title("Mean paired difference (magnified)")
        axes[2].set_ylabel(f"Distributed - Cloud ({unit})")
        for axis in axes:
            axis.set_xlabel("Peak multiplier")
            axis.set_ylabel("E2E TTFT (ms)")
            axis.set_xticks(range(1, 11))
            axis.grid(alpha=0.25)
            axis.legend()
        fig.suptitle(f"PP={pp}: Cloud vs distributed across load (NEAREST_KV)")
        fig.tight_layout()
        output_dir = FIGURE_ROOT / f"pp{pp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_dir / "peak_sweep_ttft.png", dpi=180)
        plt.close(fig)


def plot_power_cost(summary):
    for pp in (1, 2):
        subset = summary[summary["pp"] == pp]
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
        for environment in ("cloud", "distributed"):
            data = subset[subset["environment"] == environment].sort_values("peak_nx")
            axes[0].plot(data["peak_nx"], data["mean_gpu_utilization_pct"], marker="o",
                         color=COLORS[environment], label=LABELS[environment])
            axes[1].plot(data["peak_nx"], data["estimated_electricity_yen_per_hour"],
                         marker="o", color=COLORS[environment], label=LABELS[environment])
        axes[1].axhline(MIYABI_8_NODE_YEN_PER_HOUR, color="black", linestyle="--",
                        label="Miyabi 8 nodes: 306 yen/hour")
        axes[0].set_title("Mean GPU utilization")
        axes[0].set_ylabel("Utilization (%)")
        axes[1].set_title("Estimated 8-GPU electricity cost")
        axes[1].set_ylabel("Yen/hour")
        for axis in axes:
            axis.set_xlabel("Peak multiplier")
            axis.set_xticks(range(1, 11))
            axis.grid(alpha=0.25)
            axis.legend()
        fig.suptitle(f"PP={pp}: utilization-based GH200 power estimate")
        fig.tight_layout()
        output_dir = FIGURE_ROOT / f"pp{pp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_dir / "peak_sweep_power_cost.png", dpi=180)
        plt.close(fig)


def main():
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    coverage_rows = []
    for pp in (1, 2):
        for peak in range(1, 11):
            runs = {}
            paths = {}
            for environment in ("cloud", "distributed"):
                try:
                    frame, path = load_run(pp, environment, peak)
                    runs[environment] = frame
                    paths[environment] = path
                except FileNotFoundError:
                    pass
            coverage_rows.append({
                "pp": pp,
                "peak_nx": peak,
                "cloud_complete": "cloud" in runs,
                "distributed_complete": "distributed" in runs,
                "included": len(runs) == 2,
            })
            if len(runs) != 2:
                continue
            for environment in ("cloud", "distributed"):
                rows.append(summarize(pp, environment, peak, runs[environment], paths[environment]))
            plot_breakdown(pp, peak, runs)
            plot_cdf(pp, peak, runs)
    summary = pd.DataFrame(rows)
    summary.to_csv(ANALYSIS_ROOT / "ttft_summary.csv", index=False)
    pd.DataFrame(coverage_rows).to_csv(ANALYSIS_ROOT / "coverage.csv", index=False)
    paired = summary.pivot(index=["pp", "peak_nx"], columns="environment",
                           values=["mean_ttft_ms", "p50_ttft_ms", "p95_ttft_ms", "p99_ttft_ms"])
    paired.columns = [f"{metric}_{environment}" for metric, environment in paired.columns]
    paired = paired.reset_index()
    for metric in ("mean_ttft_ms", "p50_ttft_ms", "p95_ttft_ms", "p99_ttft_ms"):
        paired[f"{metric}_distributed_minus_cloud"] = (
            paired[f"{metric}_distributed"] - paired[f"{metric}_cloud"]
        )
        paired[f"{metric}_distributed_over_cloud"] = (
            paired[f"{metric}_distributed"] / paired[f"{metric}_cloud"]
        )
    paired.to_csv(ANALYSIS_ROOT / "paired_comparison.csv", index=False)
    plot_sweep(summary)
    plot_power_cost(summary)


if __name__ == "__main__":
    main()
