#!/usr/bin/env python3
"""Analyze completed PP1/PP2 workload pairs and generate interim figures."""

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
REPORTS = ROOT / "reports"

WORKLOAD_ORDER = [
    "input512_reuse00",
    "input2000_reuse025",
    "input6000_reuse05",
    "input8000_reuse00",
    "input8000_reuse025",
    "input8000_reuse05",
    "input10000_reuse00",
    "input10000_reuse025",
    "input10000_reuse05",
    "mixed_rate3p33_seed1",
]
WORKLOAD_LABELS = {
    "input512_reuse00": "512 / reuse 0%",
    "input2000_reuse025": "2000 / reuse 25%",
    "input6000_reuse05": "6000 / reuse 50%",
    "input8000_reuse00": "8000 / reuse 0%",
    "input8000_reuse025": "8000 / reuse 25%",
    "input8000_reuse05": "8000 / reuse 50%",
    "input10000_reuse00": "10000 / reuse 0%",
    "input10000_reuse025": "10000 / reuse 25%",
    "input10000_reuse05": "10000 / reuse 50%",
    "mixed_rate3p33_seed1": "Mixed / burst",
}
ARMS = ["PP1: 10 replicas", "PP2: 5 groups"]
ARM_DIRS = dict(zip(ARMS, ["pp1", "pp2"]))
ARM_COLORS = {
    "PP1: 10 replicas": "#4c78a8",
    "PP2: 5 groups": "#f58518",
}
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def completed_pairs():
    pairs = {}
    missing = []
    for workload in WORKLOAD_ORDER:
        frames = {}
        for arm, directory in ARM_DIRS.items():
            path = RESULTS / directory / workload / "requests.csv"
            if not path.exists():
                missing.append((workload, arm, "missing"))
                continue
            frame = pd.read_csv(path)
            if len(frame) != 300:
                missing.append((workload, arm, f"{len(frame)} requests"))
                continue
            frames[arm] = frame
        if len(frames) == 2:
            pairs[workload] = frames
    return pairs, missing


def breakdown(frame):
    communication = frame.communication_latency_ns / 1e6
    transfer = frame.kv_migration_latency_ns / 1e6
    scheduler = frame.queueing_before_ttft_ns / 1e6
    prefill = frame.prefill_service_ns / 1e6
    total = frame.e2e_ttft_ns / 1e6
    router = (total - scheduler - prefill - communication).clip(lower=0)
    other_communication = (communication - transfer).clip(lower=0)
    return {
        "Router queue": router.mean(),
        "Scheduler queue": scheduler.mean(),
        "KV transfer": transfer.mean(),
        "Compute / prefill": prefill.mean(),
        "RTT / other comm": other_communication.mean(),
        "Total": total.mean(),
    }


def summarize(pairs):
    rows = []
    breakdown_rows = []
    gpu_rows = []
    for workload, frames in pairs.items():
        for arm, frame in frames.items():
            ttft = frame.e2e_ttft_ns / 1e6
            tpot = frame.TPOT / 1e6
            completion = frame.request_completion_latency_ns / 1e6
            start = frame.request_send_time_ns.min()
            end = frame.request_end_time_ns.max()
            duration_s = (end - start) / 1e9
            values = breakdown(frame)
            rows.append({
                "workload": workload,
                "workload_label": WORKLOAD_LABELS[workload],
                "arm": arm,
                "requests": len(frame),
                "mean_ttft_ms": ttft.mean(),
                "p50_ttft_ms": ttft.quantile(0.50),
                "p95_ttft_ms": ttft.quantile(0.95),
                "p99_ttft_ms": ttft.quantile(0.99),
                "mean_tpot_ms": tpot.mean(),
                "mean_completion_ms": completion.mean(),
                "request_throughput_rps": len(frame) / duration_s,
                "token_throughput_tps": (frame["input"].sum() + frame.output.sum()) / duration_s,
                "redirects": int(frame.rerouted.sum()),
                "router_wait_positive_requests": int(frame.router_capacity_wait_ns.gt(0).sum()),
                "mean_router_wait_ms": frame.router_capacity_wait_ns.mean() / 1e6,
                "mean_scheduler_queue_ms": frame.queueing_before_ttft_ns.mean() / 1e6,
                "mean_prefill_ms": frame.prefill_service_ns.mean() / 1e6,
            })
            breakdown_rows.append({
                "workload": workload,
                "workload_label": WORKLOAD_LABELS[workload],
                "arm": arm,
                **values,
            })

            gpu_path = RESULTS / ARM_DIRS[arm] / workload / "gpus.csv"
            gpu = pd.read_csv(gpu_path)
            gpu_rows.append({
                "workload": workload,
                "workload_label": WORKLOAD_LABELS[workload],
                "arm": arm,
                "logical_instances": len(gpu),
                "mean_utilization_pct": gpu.utilization_pct.mean(),
                "min_utilization_pct": gpu.utilization_pct.min(),
                "max_utilization_pct": gpu.utilization_pct.max(),
                "std_utilization_pct": gpu.utilization_pct.std(ddof=0),
                "cv_utilization": gpu.utilization_pct.std(ddof=0) / gpu.utilization_pct.mean(),
                "completed_batch_count": gpu.completed_batch_count.sum(),
            })

    summary = pd.DataFrame(rows)
    components = pd.DataFrame(breakdown_rows)
    gpu_summary = pd.DataFrame(gpu_rows)
    paired_rows = []
    for workload in pairs:
        group = summary[summary.workload.eq(workload)].set_index("arm")
        pp1 = group.loc[ARMS[0]]
        pp2 = group.loc[ARMS[1]]
        paired_rows.append({
            "workload": workload,
            "workload_label": WORKLOAD_LABELS[workload],
            "mean_ttft_improvement_pct": (pp1.mean_ttft_ms - pp2.mean_ttft_ms) / pp1.mean_ttft_ms * 100,
            "p95_ttft_improvement_pct": (pp1.p95_ttft_ms - pp2.p95_ttft_ms) / pp1.p95_ttft_ms * 100,
            "p99_ttft_improvement_pct": (pp1.p99_ttft_ms - pp2.p99_ttft_ms) / pp1.p99_ttft_ms * 100,
            "scheduler_queue_reduction_pct": (pp1.mean_scheduler_queue_ms - pp2.mean_scheduler_queue_ms) / pp1.mean_scheduler_queue_ms * 100,
            "tpot_change_pct": (pp2.mean_tpot_ms - pp1.mean_tpot_ms) / pp1.mean_tpot_ms * 100,
            "completion_change_pct": (pp2.mean_completion_ms - pp1.mean_completion_ms) / pp1.mean_completion_ms * 100,
            "request_throughput_change_pct": (pp2.request_throughput_rps - pp1.request_throughput_rps) / pp1.request_throughput_rps * 100,
            "pp1_redirects": int(pp1.redirects),
            "pp2_redirects": int(pp2.redirects),
        })
    return summary, components, gpu_summary, pd.DataFrame(paired_rows)


def plot_percentiles(summary):
    metrics = [
        ("mean_ttft_ms", "Mean"),
        ("p50_ttft_ms", "p50"),
        ("p95_ttft_ms", "p95"),
        ("p99_ttft_ms", "p99"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    x = np.arange(len(summary.workload.unique()))
    width = 0.36
    for axis, (metric, title) in zip(axes.flat, metrics):
        for offset, arm in zip((-width / 2, width / 2), ARMS):
            selected = summary[summary.arm.eq(arm)].set_index("workload").reindex(summary.workload.unique())
            bars = axis.bar(x + offset, selected[metric], width, color=ARM_COLORS[arm], label=arm)
            axis.bar_label(bars, fmt="%.0f", padding=2, fontsize=8)
        axis.set_title(f"{title} E2E TTFT")
        axis.set_ylabel("ms")
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    labels = [WORKLOAD_LABELS[w] for w in summary.workload.unique()]
    for axis in axes[-1]:
        axis.set_xticks(x, labels, rotation=18, ha="right")
    axes[0, 0].legend(frameon=False)
    fig.suptitle("PP1 replicas vs PP2 groups: E2E TTFT percentiles", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGURES / "ttft_percentiles.png", dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_breakdown(components):
    def format_ms(value):
        if value == 0:
            return "0"
        if value < 1:
            return f"{value:.2f}"
        return f"{value:.1f}"

    workloads = list(components.workload.unique())
    ncols = 2
    nrows = int(np.ceil(len(workloads) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, 5 * nrows), squeeze=False)
    for axis, workload in zip(axes.flat, workloads):
        group = components[components.workload.eq(workload)].set_index("arm").reindex(ARMS)
        positions = np.arange(len(ARMS)) * 1.45
        left = np.zeros(len(ARMS))
        maximum = group.Total.max()
        for component, color in COMPONENTS:
            widths = group[component].to_numpy()
            axis.barh(positions, widths, left=left, height=0.58, color=color,
                      edgecolor="#faf8f4", linewidth=2, label=component)
            for position, start, width in zip(positions, left, widths):
                if width >= maximum * 0.075 and width >= 12:
                    axis.text(start + width / 2, position, f"{format_ms(width)} ms",
                              ha="center", va="center", color="white", fontsize=9,
                              fontweight="bold")
            left += widths
        for position, (_, row) in zip(positions, group.iterrows()):
            total = row.Total
            axis.text(total + maximum * 0.015, position, f"{total:.0f} ms",
                      va="center", fontweight="bold")
            details = "  |  ".join(
                f"{component}: {format_ms(row[component])} ms"
                for component, _ in COMPONENTS
            )
            axis.text(0, position + 0.43, details, va="center", fontsize=7.5,
                      color="#38332d")
        axis.set_yticks(positions, ARMS)
        axis.set_ylim(positions[-1] + 0.72, -0.48)
        axis.set_xlim(0, maximum * 1.22)
        axis.set_title(WORKLOAD_LABELS[workload])
        axis.set_xlabel("mean E2E TTFT components (ms)")
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    for axis in axes.flat[len(workloads):]:
        axis.set_visible(False)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS), frameon=False)
    fig.suptitle("PP1 vs PP2: mean E2E TTFT component breakdown", fontsize=17)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(FIGURES / "ttft_breakdown.png", dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cdfs(pairs):
    ncols = 2
    nrows = int(np.ceil(len(pairs) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 5 * nrows), squeeze=False)
    for axis, (workload, frames) in zip(axes.flat, pairs.items()):
        for arm in ARMS:
            values = np.sort(frames[arm].e2e_ttft_ns.to_numpy() / 1e6)
            cdf = np.arange(1, len(values) + 1) / len(values)
            axis.plot(values, cdf, linewidth=2.2, color=ARM_COLORS[arm], label=arm)
        axis.set_title(WORKLOAD_LABELS[workload])
        axis.set_xlabel("E2E TTFT (ms)")
        axis.set_ylabel("CDF")
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    for axis in axes.flat[len(pairs):]:
        axis.set_visible(False)
    axes[0, 0].legend(frameon=False)
    fig.suptitle("PP1 vs PP2: per-request E2E TTFT CDF", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGURES / "ttft_cdf.png", dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_tradeoffs(summary):
    metrics = [
        ("mean_ttft_ms", "Mean E2E TTFT", "ms"),
        ("mean_tpot_ms", "Mean TPOT", "ms/token"),
        ("mean_completion_ms", "Mean completion latency", "ms"),
        ("request_throughput_rps", "Request throughput", "requests/s"),
    ]
    workloads = list(summary.workload.unique())
    x = np.arange(len(workloads))
    width = 0.36
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    for axis, (metric, title, unit) in zip(axes.flat, metrics):
        for offset, arm in zip((-width / 2, width / 2), ARMS):
            group = summary[summary.arm.eq(arm)].set_index("workload").reindex(workloads)
            axis.bar(x + offset, group[metric], width, color=ARM_COLORS[arm], label=arm)
        axis.set_title(title)
        axis.set_ylabel(unit)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    labels = [WORKLOAD_LABELS[w] for w in workloads]
    for axis in axes[-1]:
        axis.set_xticks(x, labels, rotation=18, ha="right")
    axes[0, 0].legend(frameon=False)
    fig.suptitle("PP2 TTFT benefit vs decode/completion trade-off", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGURES / "latency_throughput_tradeoff.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_utilization(gpu_summary):
    workloads = list(gpu_summary.workload.unique())
    x = np.arange(len(workloads))
    width = 0.36
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    for axis, metric, title, ylabel in (
        (axes[0], "mean_utilization_pct", "Mean logical-instance utilization", "%"),
        (axes[1], "cv_utilization", "Utilization imbalance", "coefficient of variation"),
    ):
        for offset, arm in zip((-width / 2, width / 2), ARMS):
            group = gpu_summary[gpu_summary.arm.eq(arm)].set_index("workload").reindex(workloads)
            axis.bar(x + offset, group[metric], width, color=ARM_COLORS[arm], label=arm)
        axis.set_xticks(x, [WORKLOAD_LABELS[w] for w in workloads], rotation=18, ha="right")
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    axes[0].legend(frameon=False)
    fig.suptitle("Logical-instance utilization (PP2 is not per-stage utilization)", fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIGURES / "logical_instance_utilization.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def summarize_input8000_redirects(pairs):
    rows = []
    cohorts = [
        ("All requests", lambda frame: frame.index == frame.index),
        ("Redirected only", lambda frame: frame.rerouted.astype(bool)),
        ("Not redirected", lambda frame: ~frame.rerouted.astype(bool)),
    ]
    for workload, frames in pairs.items():
        if not workload.startswith("input8000_"):
            continue
        for arm, frame in frames.items():
            for cohort, selector in cohorts:
                selected = frame.loc[selector(frame)]
                rows.append({
                    "workload": workload,
                    "workload_label": WORKLOAD_LABELS[workload],
                    "arm": arm,
                    "cohort": cohort,
                    "requests": len(selected),
                    "mean_ttft_ms": selected.e2e_ttft_ns.mean() / 1e6,
                })
    return pd.DataFrame(rows)


def summarize_input8000_redirect_breakdown(pairs):
    rows = []
    cohorts = [
        ("All requests", lambda frame: frame.index == frame.index),
        ("Redirected only", lambda frame: frame.rerouted.astype(bool)),
        ("Not redirected", lambda frame: ~frame.rerouted.astype(bool)),
    ]
    for workload, frames in pairs.items():
        if not workload.startswith("input8000_"):
            continue
        for cohort, selector in cohorts:
            for arm, frame in frames.items():
                selected = frame.loc[selector(frame)]
                if selected.empty:
                    continue
                rows.append({
                    "workload": workload,
                    "workload_label": WORKLOAD_LABELS[workload],
                    "cohort": cohort,
                    "arm": arm,
                    "requests": len(selected),
                    **breakdown(selected),
                })
    return pd.DataFrame(rows)


def plot_input8000_redirects(summary):
    if summary.empty:
        return
    workloads = list(summary.workload.unique())
    cohorts = ["All requests", "Redirected only", "Not redirected"]
    fig, axes = plt.subplots(1, len(workloads), figsize=(7.5 * len(workloads), 6),
                             squeeze=False)
    x = np.arange(len(cohorts))
    width = 0.36
    for axis, workload in zip(axes.flat, workloads):
        workload_rows = summary[summary.workload.eq(workload)]
        for offset, arm in zip((-width / 2, width / 2), ARMS):
            group = workload_rows[workload_rows.arm.eq(arm)].set_index("cohort").reindex(cohorts)
            bars = axis.bar(x + offset, group.mean_ttft_ms, width,
                            color=ARM_COLORS[arm], label=arm)
            for bar, value, count in zip(bars, group.mean_ttft_ms, group.requests):
                if pd.notna(value):
                    axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                              f"{value:.0f} ms\n(n={int(count)})", ha="center",
                              va="bottom", fontsize=8)
        axis.set_xticks(x, cohorts)
        axis.set_title(WORKLOAD_LABELS[workload])
        axis.set_ylabel("Mean E2E TTFT (ms)")
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.margins(y=0.18)
    axes[0, 0].legend(frameon=False)
    fig.suptitle("Input 8000: TTFT by redirect outcome", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIGURES / "input8000_redirect_ttft.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_input8000_redirect_breakdown(summary):
    if summary.empty:
        return

    def format_ms(value):
        if value == 0:
            return "0"
        if value < 1:
            return f"{value:.2f}"
        return f"{value:.1f}"

    workloads = list(summary.workload.unique())
    cohorts = ["All requests", "Redirected only", "Not redirected"]
    fig, axes = plt.subplots(1, len(workloads), figsize=(9 * len(workloads), 10),
                             squeeze=False)
    for axis, workload in zip(axes.flat, workloads):
        group = summary[summary.workload.eq(workload)].copy()
        positions = np.arange(len(cohorts) * len(ARMS)) * 1.15
        labels = []
        ordered_rows = []
        for cohort in cohorts:
            for arm in ARMS:
                row = group[group.cohort.eq(cohort) & group.arm.eq(arm)].iloc[0]
                ordered_rows.append(row)
                labels.append(f"{cohort}\n{arm}")
        left = np.zeros(len(ordered_rows))
        maximum = max(row.Total for row in ordered_rows)
        for component, color in COMPONENTS:
            widths = np.array([row[component] for row in ordered_rows])
            axis.barh(positions, widths, left=left, height=0.68, color=color,
                      edgecolor="#faf8f4", linewidth=2, label=component)
            for position, start, width in zip(positions, left, widths):
                if width >= maximum * 0.075 and width >= 12:
                    axis.text(start + width / 2, position, f"{format_ms(width)} ms",
                              ha="center", va="center", color="white", fontsize=8,
                              fontweight="bold")
            left += widths
        for position, row in zip(positions, ordered_rows):
            axis.text(row.Total + maximum * 0.015, position,
                      f"{row.Total:.0f} ms (n={int(row.requests)})",
                      va="center", fontweight="bold", fontsize=9)
        axis.set_yticks(positions, labels)
        axis.set_ylim(positions[-1] + 0.7, -0.7)
        axis.set_xlim(0, maximum * 1.30)
        axis.set_title(WORKLOAD_LABELS[workload])
        axis.set_xlabel("Mean E2E TTFT components (ms)")
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS), frameon=False)
    fig.suptitle("Input 8000: mean E2E TTFT breakdown by redirect outcome", fontsize=17)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    fig.savefig(FIGURES / "input8000_redirect_ttft_breakdown.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def write_report(summary, paired, missing):
    lines = [
        "# Interim PP1 vs PP2 analysis",
        "",
        "This report uses only completed 300-request pairs. Positive improvement means PP2 is better.",
        "",
        "## Coverage",
        "",
        f"- Completed comparable pairs: {paired.shape[0]} / {len(WORKLOAD_ORDER)}",
    ]
    for workload, arm, reason in missing:
        lines.append(f"- Missing: `{workload}` / {arm} ({reason})")
    lines.extend([
        "",
        "## Paired result",
        "",
        "| Workload | Mean TTFT improvement | p95 improvement | p99 improvement | Scheduler queue reduction | TPOT change | Completion change | Redirects PP1→PP2 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in paired.itertuples():
        lines.append(
            f"| {row.workload_label} | {row.mean_ttft_improvement_pct:+.1f}% "
            f"| {row.p95_ttft_improvement_pct:+.1f}% | {row.p99_ttft_improvement_pct:+.1f}% "
            f"| {row.scheduler_queue_reduction_pct:+.1f}% | {row.tpot_change_pct:+.1f}% "
            f"| {row.completion_change_pct:+.1f}% | {row.pp1_redirects}→{row.pp2_redirects} |"
        )
    lines.extend([
        "",
        "## Current interpretation",
        "",
        "- PP2 reduces mean scheduler queue in every completed pair.",
        "- PP2 removes capacity redirects in the completed 6000-token and mixed workloads.",
        "- Mean and tail TTFT generally improve, although the 2000-token p95 regresses.",
        "- TPOT and end-to-end completion latency regress in every completed pair; PP2 is a TTFT/tail optimization, not an overall decode-speedup in these results.",
        "- PP2 utilization rows represent five logical instances, not ten physical pipeline stages. Stage-level balance cannot be inferred from this CSV.",
        "- The missing PP2 10000-token result is the strongest capacity-pressure case, so the capacity-bound conclusion remains provisional.",
        "",
        "## Figures",
        "",
        "- `figures/ttft_percentiles.png`",
        "- `figures/ttft_breakdown.png`",
        "- `figures/ttft_cdf.png`",
        "- `figures/latency_throughput_tradeoff.png`",
        "- `figures/logical_instance_utilization.png`",
        "- `figures/input8000_redirect_ttft.png`",
        "- `figures/input8000_redirect_ttft_breakdown.png`",
        "",
    ])
    (REPORTS / "interim_pp1_vs_pp2.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    pairs, missing = completed_pairs()
    if not pairs:
        raise RuntimeError("No completed PP1/PP2 pairs found")
    summary, components, gpu_summary, paired = summarize(pairs)
    input8000_redirects = summarize_input8000_redirects(pairs)
    input8000_redirect_breakdown = summarize_input8000_redirect_breakdown(pairs)
    summary.to_csv(ANALYSIS / "comparison_summary.csv", index=False)
    components.to_csv(ANALYSIS / "ttft_breakdown.csv", index=False)
    gpu_summary.to_csv(ANALYSIS / "logical_instance_utilization.csv", index=False)
    paired.to_csv(ANALYSIS / "paired_improvements.csv", index=False)
    input8000_redirects.to_csv(ANALYSIS / "input8000_redirect_ttft.csv", index=False)
    input8000_redirect_breakdown.to_csv(
        ANALYSIS / "input8000_redirect_ttft_breakdown.csv", index=False
    )
    plot_percentiles(summary)
    plot_breakdown(components)
    plot_cdfs(pairs)
    plot_tradeoffs(summary)
    plot_utilization(gpu_summary)
    plot_input8000_redirects(input8000_redirects)
    plot_input8000_redirect_breakdown(input8000_redirect_breakdown)
    write_report(summary, paired, missing)
    print(f"Analyzed {len(pairs)} completed PP1/PP2 pairs")
    print(paired.to_string(index=False))
    print(f"Figures: {FIGURES}")
    print(f"Report: {REPORTS / 'interim_pp1_vs_pp2.md'}")


if __name__ == "__main__":
    main()
