#!/usr/bin/env python3
"""Analyze TTFT and batch-busy GPU utilization for the five-policy run."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"
REPORT = ROOT / "report.md"
POLICIES = [
    ("A: Nearest only", "NEAREST_KV"),
    ("B: Redirect / cold", "NEAREST_MIGRATE"),
    ("C: Redirect / KV", "NEAREST_MIGRATE_KV"),
    ("D: Multi no model", "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"),
    ("E: Multi learned", "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"),
]
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]
MULTI_COMPARISON_POLICIES = POLICIES[2:]
REDIRECT_GROUPS = [
    ("All requests", None),
    ("Redirected only", True),
    ("Not redirected only", False),
]


def load_results():
    requests = {}
    gpus = {}
    timeseries = {}
    missing = []
    for label, policy in POLICIES:
        result_dir = RESULTS / policy
        paths = {
            "requests": result_dir / "requests.csv",
            "gpus": result_dir / "gpus.csv",
            "timeseries": result_dir / "gpu_utilization_timeseries.csv",
        }
        absent = [name for name, path in paths.items() if not path.exists()]
        if absent:
            missing.append(f"{policy}: {', '.join(absent)}")
            continue
        requests[label] = pd.read_csv(paths["requests"])
        gpus[label] = pd.read_csv(paths["gpus"])
        timeseries[label] = pd.read_csv(paths["timeseries"])
    if missing:
        raise FileNotFoundError(
            "Five-policy simulation outputs are incomplete:\n" + "\n".join(missing)
        )
    return requests, gpus, timeseries


def breakdown(frame):
    communication = frame.communication_latency_ns.mean() / 1e6
    transfer = frame.kv_migration_latency_ns.mean() / 1e6
    router_queue = (
        frame.e2e_ttft_ns
        - frame.prefill_service_ns
        - frame.communication_latency_ns
        - frame.queueing_before_ttft_ns
    ).clip(lower=0).mean() / 1e6
    return {
        "Router queue": router_queue,
        "Scheduler queue": frame.queueing_before_ttft_ns.mean() / 1e6,
        "KV transfer": transfer,
        "Compute / prefill": frame.prefill_service_ns.mean() / 1e6,
        "RTT / other comm": max(0.0, communication - transfer),
        "Total": frame.e2e_ttft_ns.mean() / 1e6,
    }


def make_summaries(requests, gpus):
    ttft_rows = []
    gpu_rows = []
    for label, _ in POLICIES:
        frame = requests[label]
        ttft = frame.e2e_ttft_ns / 1e6
        ttft_rows.append({
            "policy": label,
            "requests": len(frame),
            "mean_ttft_ms": ttft.mean(),
            "p50_ttft_ms": ttft.quantile(0.50),
            "p95_ttft_ms": ttft.quantile(0.95),
            "p99_ttft_ms": ttft.quantile(0.99),
            "redirects": int(frame.rerouted.sum()),
            **breakdown(frame),
        })
        gpu = gpus[label]
        gpu_rows.append({
            "policy": label,
            "gpu_count": len(gpu),
            "mean_utilization_pct": gpu.utilization_pct.mean(),
            "min_utilization_pct": gpu.utilization_pct.min(),
            "max_utilization_pct": gpu.utilization_pct.max(),
            "std_utilization_pct": gpu.utilization_pct.std(ddof=0),
            "cv_utilization": (
                gpu.utilization_pct.std(ddof=0) / gpu.utilization_pct.mean()
                if gpu.utilization_pct.mean() > 0 else 0.0
            ),
            "total_busy_time_s": gpu.busy_time_ns.sum() / 1e9,
            "total_idle_time_s": gpu.idle_time_ns.sum() / 1e9,
        })
    ttft_summary = pd.DataFrame(ttft_rows)
    gpu_summary = pd.DataFrame(gpu_rows)
    ttft_summary.to_csv(ANALYSIS / "ttft_summary.csv", index=False)
    gpu_summary.to_csv(ANALYSIS / "gpu_utilization_summary.csv", index=False)
    return ttft_summary, gpu_summary


def plot_ttft_breakdown(ttft_summary):
    positions = np.arange(len(POLICIES))
    left = np.zeros(len(POLICIES))
    maximum = ttft_summary.Total.max()
    fig, axis = plt.subplots(figsize=(15, 8.5))
    for component, color in COMPONENTS:
        widths = ttft_summary[component].to_numpy()
        axis.barh(positions, widths, left=left, height=0.62, color=color,
                  edgecolor="#faf8f4", linewidth=2, label=component)
        left += widths
    for position, row in enumerate(ttft_summary.itertuples()):
        axis.text(row.Total + maximum * 0.015, position,
                  f"{row.Total:.0f} ms", va="center", fontweight="bold")
    axis.set_yticks(positions, [label for label, _ in POLICIES])
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.18)
    axis.set_xlabel("mean E2E TTFT components (ms)")
    axis.set_title("Prompt 6000 / reuse 50% / 90s: five-policy TTFT breakdown")
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(loc="lower right", ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(FIGURES / "five_policy_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def make_redirect_breakdown(requests):
    rows = []
    for label, _ in MULTI_COMPARISON_POLICIES:
        frame = requests[label]
        for group_label, redirected in REDIRECT_GROUPS:
            subset = frame if redirected is None else frame[frame.rerouted.astype(bool).eq(redirected)]
            if subset.empty:
                continue
            rows.append({
                "redirect_group": group_label,
                "policy": label,
                "requests": len(subset),
                **breakdown(subset),
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(
        ANALYSIS / "cde_ttft_breakdown_by_redirect_status.csv", index=False
    )
    return summary


def plot_redirect_breakdown(summary):
    labels = [label for label, _ in MULTI_COMPARISON_POLICIES]
    maximum = summary.Total.max()
    fig, axes = plt.subplots(1, 3, figsize=(19, 6.5), sharex=True, sharey=True)
    for axis, (group_label, _) in zip(axes, REDIRECT_GROUPS):
        group = (
            summary[summary.redirect_group.eq(group_label)]
            .set_index("policy")
            .reindex(labels)
        )
        positions = np.arange(len(labels))
        left = np.zeros(len(labels))
        for component, color in COMPONENTS:
            widths = group[component].fillna(0).to_numpy()
            axis.barh(
                positions, widths, left=left, height=0.58, color=color,
                edgecolor="#faf8f4", linewidth=2, label=component,
            )
            left += widths
        for position, (_, row) in enumerate(group.iterrows()):
            if pd.isna(row.Total):
                continue
            axis.text(
                row.Total + maximum * 0.015,
                position,
                f"{row.Total:.0f} ms\n(n={int(row.requests)})",
                va="center",
                fontweight="bold",
                fontsize=9,
            )
        axis.set_title(group_label)
        axis.set_yticks(positions, labels)
        axis.tick_params(axis="y", labelleft=True)
        axis.invert_yaxis()
        axis.set_xlim(0, maximum * 1.24)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    axes[1].set_xlabel("mean E2E TTFT components (ms)")
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, legend_labels, loc="lower center", ncol=len(COMPONENTS),
        frameon=False,
    )
    fig.suptitle(
        "Prompt 6000 / reuse 50% / 90s: C–E mean E2E TTFT breakdown "
        "by redirect status",
        fontsize=17,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.94))
    fig.savefig(
        FIGURES / "cde_ttft_breakdown_by_redirect_status.png",
        dpi=180,
        bbox_inches="tight",
        facecolor="#faf8f4",
    )
    plt.close(fig)


def plot_utilization_timeseries(timeseries):
    gpu_ids = sorted(set().union(*(set(frame.gpu_id) for frame in timeseries.values())))
    colors = plt.cm.tab10(np.linspace(0, 1, max(10, len(gpu_ids))))
    fig, axes = plt.subplots(len(POLICIES), 1, figsize=(16, 16), sharex=True, sharey=True)
    for axis, (label, _) in zip(axes, POLICIES):
        frame = timeseries[label]
        for color, gpu_id in zip(colors, gpu_ids):
            gpu = frame[frame.gpu_id.eq(gpu_id)].sort_values("window_index")
            axis.step(gpu.time_since_start_s, gpu.utilization_pct, where="post",
                      color=color, linewidth=1.0, alpha=0.65, label=f"GPU {gpu_id}")
        cluster_mean = frame.groupby("window_index", as_index=False).agg(
            time_since_start_s=("time_since_start_s", "first"),
            utilization_pct=("utilization_pct", "mean"),
        )
        axis.plot(cluster_mean.time_since_start_s, cluster_mean.utilization_pct,
                  color="#171717", linewidth=2.5, label="Cluster mean")
        axis.set_title(label, loc="left")
        axis.set_ylabel("utilization (%)")
        axis.set_ylim(0, 105)
        axis.grid(color="#ded8cf", linewidth=0.6)
    axes[-1].set_xlabel("time since first request (s)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=11, frameon=False)
    fig.suptitle("Per-GPU batch-busy utilization over time (1-second windows)", fontsize=17)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(FIGURES / "gpu_utilization_timeseries.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_utilization_heatmaps(timeseries):
    fig, axes = plt.subplots(len(POLICIES), 1, figsize=(17, 14), sharex=True)
    image = None
    for axis, (label, _) in zip(axes, POLICIES):
        frame = timeseries[label]
        pivot = frame.pivot(index="gpu_id", columns="window_index", values="utilization_pct")
        image = axis.imshow(pivot, aspect="auto", interpolation="nearest",
                            cmap="YlGnBu", vmin=0, vmax=100)
        axis.set_yticks(range(len(pivot.index)), [f"GPU {gpu}" for gpu in pivot.index])
        axis.set_title(label, loc="left")
        axis.set_ylabel("GPU")
    last = timeseries[POLICIES[-1][0]]
    window_times = last.groupby("window_index").time_since_start_s.first()
    tick_positions = np.linspace(0, len(window_times) - 1, min(10, len(window_times))).astype(int)
    axes[-1].set_xticks(tick_positions, [f"{window_times.iloc[i]:.0f}" for i in tick_positions])
    axes[-1].set_xlabel("time since first request (s)")
    fig.colorbar(image, ax=axes, label="utilization (%)", fraction=0.015, pad=0.015)
    fig.suptitle("GPU utilization heatmap by routing policy", fontsize=17)
    fig.subplots_adjust(top=0.95, bottom=0.06, left=0.08, right=0.94, hspace=0.32)
    fig.savefig(FIGURES / "gpu_utilization_heatmaps.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_utilization_summary(gpus, gpu_summary):
    labels = [label for label, _ in POLICIES]
    data = [gpus[label].utilization_pct.to_numpy() for label in labels]
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    matplotlib_version = tuple(
        int(part) for part in matplotlib.__version__.split(".")[:2]
    )
    label_argument = (
        {"tick_labels": labels} if matplotlib_version >= (3, 9)
        else {"labels": labels}
    )
    boxes = axes[0].boxplot(data, vert=False, patch_artist=True,
                           showmeans=True, **label_argument)
    for patch in boxes["boxes"]:
        patch.set_facecolor("#4f83c2")
        patch.set_alpha(0.75)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("whole-run utilization per GPU (%)")
    axes[0].set_title("Per-GPU utilization distribution")
    axes[0].grid(axis="x", color="#d9d2c8", linewidth=0.8)
    positions = np.arange(len(labels))
    axes[1].barh(positions, gpu_summary.mean_utilization_pct, color="#31866f",
                 label="Mean utilization")
    for position, row in enumerate(gpu_summary.itertuples()):
        axes[1].text(row.mean_utilization_pct + 0.4, position,
                     f"mean {row.mean_utilization_pct:.1f}% / CV {row.cv_utilization:.2f}",
                     va="center")
    axes[1].set_yticks(positions, labels)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("mean utilization (%)")
    axes[1].set_title("Cluster utilization and imbalance")
    axes[1].grid(axis="x", color="#d9d2c8", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(FIGURES / "gpu_utilization_summary.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_utilization_distribution(timeseries):
    labels = [label for label, _ in POLICIES]
    colors = ["#4c78a8", "#e79b37", "#865bd6", "#31866f", "#c74b3a"]
    samples = [timeseries[label].utilization_pct.to_numpy() for label in labels]
    rows = []
    for label, values in zip(labels, samples):
        rows.append({
            "policy": label,
            "gpu_window_samples": len(values),
            "mean_pct": np.mean(values),
            "p10_pct": np.quantile(values, 0.10),
            "p25_pct": np.quantile(values, 0.25),
            "p50_pct": np.quantile(values, 0.50),
            "p75_pct": np.quantile(values, 0.75),
            "p90_pct": np.quantile(values, 0.90),
            "idle_window_fraction": np.mean(values <= 1.0),
            "under_50pct_window_fraction": np.mean(values < 50.0),
            "saturated_window_fraction": np.mean(values >= 99.0),
        })
    distribution_summary = pd.DataFrame(rows)
    distribution_summary.to_csv(
        ANALYSIS / "gpu_utilization_window_distribution.csv", index=False
    )

    fig, axes = plt.subplots(1, 3, figsize=(19, 6))
    bins = np.linspace(0, 100, 21)
    for label, color, values in zip(labels, colors, samples):
        axes[0].hist(values, bins=bins, density=True, histtype="step",
                     linewidth=2.2, color=color, label=label)
        ordered = np.sort(values)
        probability = np.arange(1, len(ordered) + 1) / len(ordered)
        axes[1].step(ordered, probability, where="post", linewidth=2.2,
                     color=color, label=label)
    axes[0].set_title("Distribution across GPU × 1-second windows")
    axes[0].set_xlabel("utilization (%)")
    axes[0].set_ylabel("density")
    axes[1].set_title("Utilization CDF")
    axes[1].set_xlabel("utilization (%)")
    axes[1].set_ylabel("cumulative probability")
    violins = axes[2].violinplot(samples, vert=False, showmeans=True,
                                 showmedians=True, showextrema=True)
    for body, color in zip(violins["bodies"], colors):
        body.set_facecolor(color)
        body.set_edgecolor(color)
        body.set_alpha(0.68)
    axes[2].set_yticks(np.arange(1, len(labels) + 1), labels)
    axes[2].invert_yaxis()
    axes[2].set_title("Per-window utilization density")
    axes[2].set_xlabel("utilization (%)")
    for axis in axes:
        axis.set_xlim(0, 100)
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    axes[0].legend(fontsize=8)
    fig.suptitle("GPU utilization distribution over time", fontsize=17)
    fig.tight_layout()
    fig.savefig(FIGURES / "gpu_utilization_window_distribution.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)
    return distribution_summary


def write_report(ttft_summary, gpu_summary, distribution_summary):
    merged = ttft_summary.merge(gpu_summary, on="policy")
    lines = [
        "# Five-policy GPU utilization analysis", "",
        "## Summary", "",
        "| Policy | Mean TTFT | p95 TTFT | Redirects | Mean GPU util | Min–max util | Util CV |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in merged.itertuples():
        lines.append(
            f"| {row.policy} | {row.mean_ttft_ms:.1f} ms | {row.p95_ttft_ms:.1f} ms | "
            f"{row.redirects} | {row.mean_utilization_pct:.1f}% | "
            f"{row.min_utilization_pct:.1f}–{row.max_utilization_pct:.1f}% | "
            f"{row.cv_utilization:.3f} |"
        )
    lines += [
        "", "## Figures", "",
        "![TTFT breakdown](figures/five_policy_ttft_breakdown.png)", "",
        "![C–E TTFT breakdown by redirect status](figures/cde_ttft_breakdown_by_redirect_status.png)", "",
        "![Utilization summary](figures/gpu_utilization_summary.png)", "",
        "![Utilization time series](figures/gpu_utilization_timeseries.png)", "",
        "![Utilization heatmaps](figures/gpu_utilization_heatmaps.png)", "",
        "![Utilization distribution](figures/gpu_utilization_window_distribution.png)", "",
        "## One-second-window distribution", "",
        "| Policy | p10 | p50 | p90 | Idle windows | Saturated windows |", 
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in distribution_summary.itertuples():
        lines.append(
            f"| {row.policy} | {row.p10_pct:.1f}% | {row.p50_pct:.1f}% | "
            f"{row.p90_pct:.1f}% | {row.idle_window_fraction * 100:.1f}% | "
            f"{row.saturated_window_fraction * 100:.1f}% |"
        )
    lines += [
        "",
        "The utilization metric is simulated batch-busy wall-clock coverage, not an SM hardware counter.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    requests, gpus, timeseries = load_results()
    ttft_summary, gpu_summary = make_summaries(requests, gpus)
    plot_ttft_breakdown(ttft_summary)
    redirect_breakdown = make_redirect_breakdown(requests)
    plot_redirect_breakdown(redirect_breakdown)
    plot_utilization_timeseries(timeseries)
    plot_utilization_heatmaps(timeseries)
    plot_utilization_summary(gpus, gpu_summary)
    distribution_summary = plot_utilization_distribution(timeseries)
    write_report(ttft_summary, gpu_summary, distribution_summary)
    print(ttft_summary.to_string(index=False))
    print(gpu_summary.to_string(index=False))
    print(distribution_summary.to_string(index=False))


if __name__ == "__main__":
    main()
