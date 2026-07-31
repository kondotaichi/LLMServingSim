#!/usr/bin/env python3
"""Analyze completed PP1/PP2/scheduler-hide/speculative workload arms and
generate comparison figures.

Arms are added incrementally as their simulations complete (see
.claude/plans/elegant-dazzling-lobster.md): PP1 and PP2 are the original
baseline pair; "PP2 + scheduler-hide" (Method A) and "PP2 + speculative"
(Method B) are new arms layered on top of PP2. completed_pairs() tolerates
any subset of ARMS being present per workload so partial rollout still
produces a report.
"""

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
# Output location for figures/analysis/reports defaults to ROOT (this
# workload directory), matching the original behavior. Override with
# ANALYSIS_OUTPUT_ROOT to write a comparison snapshot elsewhere (e.g. a
# separate write-up directory) without touching results/ or ROOT's own
# figures/analysis/reports.
OUTPUT_ROOT = Path(os.environ.get("ANALYSIS_OUTPUT_ROOT", ROOT))
ANALYSIS = OUTPUT_ROOT / "analysis"
FIGURES = OUTPUT_ROOT / "figures"
REPORTS = OUTPUT_ROOT / "reports"


def _results_dir(directory):
    """Per-arm results directory. The Method A/B arms' raw simulation
    output (results/pp2_scheduler_hide, results/pp2_spec_scheduler_hide)
    was relocated under the write-up directory (ANALYSIS_OUTPUT_ROOT) for
    self-containment; PP1/PP2 baseline output stays under ROOT/results.
    Checks OUTPUT_ROOT first so a moved arm is found there, falling back
    to ROOT for arms that were never relocated."""
    moved = OUTPUT_ROOT / "results" / directory
    if moved.exists():
        return moved
    return RESULTS / directory

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
ARMS = [
    "PP1: 10 replicas",
    "PP2: 5 groups",
    "PP2 + scheduler-hide",
    "PP2 + speculative",
]
ARM_DIRS = dict(zip(
    ARMS, ["pp1", "pp2", "pp2_scheduler_hide", "pp2_spec_scheduler_hide"]
))
ARM_COLORS = {
    "PP1: 10 replicas": "#4c78a8",
    "PP2: 5 groups": "#f58518",
    "PP2 + scheduler-hide": "#54a24b",
    "PP2 + speculative": "#b279a2",
}
# Arms where KV migration transfer is overlapped with target-scheduler
# queueing (--enable-scheduler-hide-kv-migration) instead of serialized.
# queueing_before_ttft_ns already subsumes kv_migration_effective_latency_ns
# for these arms (admission cannot happen before kv_ready_time_ns), so
# breakdown() nets the overlap out of both the "KV transfer" bar and the
# "Router queue" residual instead of double-counting wall-clock time.
SCHEDULER_HIDE_ARMS = {"PP2 + scheduler-hide", "PP2 + speculative"}
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
            path = _results_dir(directory) / workload / "requests.csv"
            if not path.exists():
                missing.append((workload, arm, "missing"))
                continue
            frame = pd.read_csv(path)
            if len(frame) != 300:
                missing.append((workload, arm, f"{len(frame)} requests"))
                continue
            frames[arm] = frame
        if frames:
            pairs[workload] = frames
    return pairs, missing


def _arms_present(container, workload=None):
    """Return the subset of ARMS actually present, in ARMS order.

    `container` is either a {workload: {arm: frame}} pairs dict (pass
    `workload` to scope to one) or a summary/components DataFrame with an
    `arm` column (pass `workload=None`).
    """
    if workload is not None:
        present = set(container[workload].keys())
    else:
        present = set(container.arm.unique())
    return [arm for arm in ARMS if arm in present]


def _grouped_bar_offsets(arms_present, width_total=0.8):
    n = max(1, len(arms_present))
    width = width_total / n
    offsets = (np.arange(n) - (n - 1) / 2) * width
    return width, offsets


def breakdown(frame, scheduler_hide=False):
    communication = frame.communication_latency_ns / 1e6
    transfer = frame.kv_migration_latency_ns / 1e6
    scheduler = frame.queueing_before_ttft_ns / 1e6
    prefill = frame.prefill_service_ns / 1e6
    total = frame.e2e_ttft_ns / 1e6
    other_communication = (communication - transfer).clip(lower=0)

    if scheduler_hide and "kv_migration_effective_latency_ns" in frame.columns:
        # Method A/B: queueing_before_ttft_ns already covers however much of
        # the KV transfer overlapped with scheduler waiting (by
        # construction, admission never happens before kv_ready_time_ns).
        # Only the portion of the *effective* (post-speculative-credit)
        # transfer that stuck out past scheduler queueing is still exposed
        # on the critical path; that's usually ~0. Netting communication
        # down to this exposed sliver (instead of the full, non-overlap-
        # aware transfer) keeps the router-queue residual honest.
        effective_transfer = frame.kv_migration_effective_latency_ns / 1e6
        exposed_transfer = (effective_transfer - scheduler).clip(lower=0)
        communication_for_router = other_communication + exposed_transfer
        transfer_display = exposed_transfer
    else:
        communication_for_router = communication
        transfer_display = transfer

    router = (total - scheduler - prefill - communication_for_router).clip(lower=0)
    return {
        "Router queue": router.mean(),
        "Scheduler queue": scheduler.mean(),
        "KV transfer": transfer_display.mean(),
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
            values = breakdown(frame, scheduler_hide=arm in SCHEDULER_HIDE_ARMS)
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

            gpu_path = _results_dir(ARM_DIRS[arm]) / workload / "gpus.csv"
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
    return summary, components, gpu_summary


def pairwise_vs_baseline(summary, baseline_arm):
    """One row per (workload, comparison arm) other than baseline_arm,
    for every workload where baseline_arm is present. Positive
    *_improvement_pct means the comparison arm is better than baseline."""
    rows = []
    for workload in summary.workload.unique():
        group = summary[summary.workload.eq(workload)].set_index("arm")
        if baseline_arm not in group.index:
            continue
        baseline = group.loc[baseline_arm]
        for arm in group.index:
            if arm == baseline_arm:
                continue
            other = group.loc[arm]
            rows.append({
                "workload": workload,
                "workload_label": WORKLOAD_LABELS[workload],
                "baseline_arm": baseline_arm,
                "comparison_arm": arm,
                "mean_ttft_improvement_pct": (baseline.mean_ttft_ms - other.mean_ttft_ms) / baseline.mean_ttft_ms * 100,
                "p95_ttft_improvement_pct": (baseline.p95_ttft_ms - other.p95_ttft_ms) / baseline.p95_ttft_ms * 100,
                "p99_ttft_improvement_pct": (baseline.p99_ttft_ms - other.p99_ttft_ms) / baseline.p99_ttft_ms * 100,
                "scheduler_queue_reduction_pct": (baseline.mean_scheduler_queue_ms - other.mean_scheduler_queue_ms) / baseline.mean_scheduler_queue_ms * 100,
                "tpot_change_pct": (other.mean_tpot_ms - baseline.mean_tpot_ms) / baseline.mean_tpot_ms * 100,
                "completion_change_pct": (other.mean_completion_ms - baseline.mean_completion_ms) / baseline.mean_completion_ms * 100,
                "request_throughput_change_pct": (other.request_throughput_rps - baseline.request_throughput_rps) / baseline.request_throughput_rps * 100,
                "baseline_redirects": int(baseline.redirects),
                "comparison_redirects": int(other.redirects),
            })
    return pd.DataFrame(rows)


def plot_percentiles(summary):
    metrics = [
        ("mean_ttft_ms", "Mean"),
        ("p50_ttft_ms", "p50"),
        ("p95_ttft_ms", "p95"),
        ("p99_ttft_ms", "p99"),
    ]
    workloads = list(summary.workload.unique())
    arms_present = _arms_present(summary)
    width, offsets = _grouped_bar_offsets(arms_present)
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    x = np.arange(len(workloads))
    for axis, (metric, title) in zip(axes.flat, metrics):
        for offset, arm in zip(offsets, arms_present):
            selected = summary[summary.arm.eq(arm)].set_index("workload").reindex(workloads)
            bars = axis.bar(x + offset, selected[metric], width, color=ARM_COLORS[arm], label=arm)
            axis.bar_label(bars, fmt="%.0f", padding=2, fontsize=8)
        axis.set_title(f"{title} E2E TTFT")
        axis.set_ylabel("ms")
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    labels = [WORKLOAD_LABELS[w] for w in workloads]
    for axis in axes[-1]:
        axis.set_xticks(x, labels, rotation=18, ha="right")
    axes[0, 0].legend(frameon=False)
    fig.suptitle("PP arms: E2E TTFT percentiles", fontsize=17)
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
        workload_components = components[components.workload.eq(workload)]
        arms_here = _arms_present(workload_components)
        group = workload_components.set_index("arm").reindex(arms_here)
        positions = np.arange(len(arms_here)) * 1.45
        left = np.zeros(len(arms_here))
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
        axis.set_yticks(positions, arms_here)
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
    fig.suptitle("PP arms: mean E2E TTFT component breakdown", fontsize=17)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(FIGURES / "ttft_breakdown.png", dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cdfs(pairs):
    workload = "input8000_reuse025"
    if workload not in pairs:
        return
    cohorts = [
        ("All requests", lambda frame: frame.index == frame.index,
         "input8000_reuse025_ttft_cdf_all.png"),
        ("Redirected requests only", lambda frame: frame.rerouted.astype(bool),
         "input8000_reuse025_ttft_cdf_redirected.png"),
        ("Non-redirected requests only", lambda frame: ~frame.rerouted.astype(bool),
         "input8000_reuse025_ttft_cdf_not_redirected.png"),
    ]
    arm_labels = [
        ("PP2: 5 groups", "PP=2"),
        ("PP1: 10 replicas", "PP=1"),
    ]
    frames = pairs[workload]
    for title, selector, filename in cohorts:
        fig, axis = plt.subplots(figsize=(8.5, 5.2))
        for arm, label in arm_labels:
            if arm not in frames:
                continue
            selected = frames[arm].loc[selector(frames[arm])]
            if selected.empty:
                continue
            values = np.sort(selected.e2e_ttft_ns.to_numpy() / 1e6)
            cdf = np.arange(1, len(values) + 1) / len(values)
            axis.plot(values, cdf, linewidth=2.2, color=ARM_COLORS[arm],
                      label=f"{label} (n={len(selected)})")
        axis.set_title(f"Input 8000 / reuse 25%: {title}", fontsize=15)
        axis.set_xlabel("E2E TTFT (ms)")
        axis.set_ylabel("CDF")
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(FIGURES / filename, dpi=180, bbox_inches="tight",
                    facecolor="#faf8f4")
        plt.close(fig)


def plot_tradeoffs(summary):
    metrics = [
        ("mean_ttft_ms", "Mean E2E TTFT", "ms"),
        ("mean_tpot_ms", "Mean TPOT", "ms/token"),
        ("mean_completion_ms", "Mean completion latency", "ms"),
        ("request_throughput_rps", "Request throughput", "requests/s"),
    ]
    workloads = list(summary.workload.unique())
    arms_present = _arms_present(summary)
    width, offsets = _grouped_bar_offsets(arms_present)
    x = np.arange(len(workloads))
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    for axis, (metric, title, unit) in zip(axes.flat, metrics):
        for offset, arm in zip(offsets, arms_present):
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
    fig.suptitle("PP arms: TTFT benefit vs decode/completion trade-off", fontsize=17)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGURES / "latency_throughput_tradeoff.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_utilization(gpu_summary):
    workloads = list(gpu_summary.workload.unique())
    arms_present = _arms_present(gpu_summary)
    width, offsets = _grouped_bar_offsets(arms_present)
    x = np.arange(len(workloads))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    for axis, metric, title, ylabel in (
        (axes[0], "mean_utilization_pct", "Mean logical-instance utilization", "%"),
        (axes[1], "cv_utilization", "Utilization imbalance", "coefficient of variation"),
    ):
        for offset, arm in zip(offsets, arms_present):
            group = gpu_summary[gpu_summary.arm.eq(arm)].set_index("workload").reindex(workloads)
            axis.bar(x + offset, group[metric], width, color=ARM_COLORS[arm], label=arm)
        axis.set_xticks(x, [WORKLOAD_LABELS[w] for w in workloads], rotation=18, ha="right")
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    axes[0].legend(frameon=False)
    fig.suptitle("Logical-instance utilization (PP2 arms are not per-stage utilization)", fontsize=16)
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
                    **breakdown(selected, scheduler_hide=arm in SCHEDULER_HIDE_ARMS),
                })
    return pd.DataFrame(rows)


def plot_input8000_redirects(summary):
    if summary.empty:
        return
    workloads = list(summary.workload.unique())
    cohorts = ["All requests", "Redirected only", "Not redirected"]
    arms_present = _arms_present(summary)
    width, offsets = _grouped_bar_offsets(arms_present)
    fig, axes = plt.subplots(1, len(workloads), figsize=(7.5 * len(workloads), 6),
                             squeeze=False)
    x = np.arange(len(cohorts))
    for axis, workload in zip(axes.flat, workloads):
        workload_rows = summary[summary.workload.eq(workload)]
        for offset, arm in zip(offsets, arms_present):
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

    workload = "input8000_reuse025"
    cohort_outputs = [
        ("All requests", "All requests", "input8000_reuse025_ttft_breakdown_all.png"),
        ("Redirected only", "Redirected requests only",
         "input8000_reuse025_ttft_breakdown_redirected.png"),
        ("Not redirected", "Non-redirected requests only",
         "input8000_reuse025_ttft_breakdown_not_redirected.png"),
    ]
    arm_labels = [
        ("PP2: 5 groups", "PP=2"),
        ("PP1: 10 replicas", "PP=1"),
    ]
    workload_rows = summary[summary.workload.eq(workload)]
    for cohort, title, filename in cohort_outputs:
        group = workload_rows[workload_rows.cohort.eq(cohort)]
        ordered_rows = []
        labels = []
        for arm, label in arm_labels:
            matches = group[group.arm.eq(arm)]
            if matches.empty:
                continue
            ordered_rows.append(matches.iloc[0])
            labels.append(label)
        if not ordered_rows:
            continue

        fig, axis = plt.subplots(figsize=(9, 3.6))
        positions = np.arange(len(ordered_rows))
        left = np.zeros(len(ordered_rows))
        maximum = max((row.Total for row in ordered_rows), default=0)
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
        axis.set_title(f"Input 8000 / reuse 25%: {title}", fontsize=15)
        axis.set_xlabel("Mean E2E TTFT components (ms)")
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
        handles, legend_labels = axis.get_legend_handles_labels()
        fig.legend(handles, legend_labels, loc="lower center",
                   ncol=len(COMPONENTS), frameon=False)
        fig.tight_layout(rect=(0, 0.16, 1, 1))
        fig.savefig(FIGURES / filename, dpi=180, bbox_inches="tight",
                    facecolor="#faf8f4")
        plt.close(fig)


def write_report(summary, paired, paired_vs_pp2, missing):
    lines = [
        "# Interim PP arm comparison",
        "",
        "This report uses only completed 300-request arms. Positive improvement means "
        "the comparison arm is better than the stated baseline.",
        "",
        "## Coverage",
        "",
        f"- Arms defined: {', '.join(ARMS)}",
        f"- Completed (workload, arm) cells: {len(missing)} missing entries listed below "
        f"out of {len(WORKLOAD_ORDER) * len(ARMS)} possible.",
    ]
    for workload, arm, reason in missing:
        lines.append(f"- Missing: `{workload}` / {arm} ({reason})")
    lines.extend([
        "",
        "## Paired result (baseline: PP1)",
        "",
        "| Workload | Comparison arm | Mean TTFT improvement | p95 improvement | p99 improvement | Scheduler queue reduction | TPOT change | Completion change | Redirects baseline→comparison |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in paired.itertuples():
        lines.append(
            f"| {row.workload_label} | {row.comparison_arm} "
            f"| {row.mean_ttft_improvement_pct:+.1f}% "
            f"| {row.p95_ttft_improvement_pct:+.1f}% | {row.p99_ttft_improvement_pct:+.1f}% "
            f"| {row.scheduler_queue_reduction_pct:+.1f}% | {row.tpot_change_pct:+.1f}% "
            f"| {row.completion_change_pct:+.1f}% | {row.baseline_redirects}→{row.comparison_redirects} |"
        )
    lines.extend([
        "",
        "## Paired result (baseline: PP2, isolates Method A/B's incremental effect)",
        "",
        "| Workload | Comparison arm | Mean TTFT improvement | p95 improvement | p99 improvement | Scheduler queue reduction | TPOT change | Completion change | Redirects baseline→comparison |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in paired_vs_pp2.itertuples():
        lines.append(
            f"| {row.workload_label} | {row.comparison_arm} "
            f"| {row.mean_ttft_improvement_pct:+.1f}% "
            f"| {row.p95_ttft_improvement_pct:+.1f}% | {row.p99_ttft_improvement_pct:+.1f}% "
            f"| {row.scheduler_queue_reduction_pct:+.1f}% | {row.tpot_change_pct:+.1f}% "
            f"| {row.completion_change_pct:+.1f}% | {row.baseline_redirects}→{row.comparison_redirects} |"
        )
    lines.extend([
        "",
        "## Current interpretation",
        "",
        "- PP2 reduces mean scheduler queue in every completed pair vs PP1.",
        "- PP2 removes capacity redirects in the completed 6000-token and mixed workloads.",
        "- Mean and tail TTFT generally improve PP1->PP2, although the 2000-token p95 regresses.",
        "- TPOT and end-to-end completion latency regress PP1->PP2 in every completed pair; "
        "PP2 is a TTFT/tail optimization, not an overall decode-speedup in these results.",
        "- PP2 utilization rows represent five logical instances, not ten physical pipeline "
        "stages. Stage-level balance cannot be inferred from this CSV.",
        "- For 'PP2 + scheduler-hide'/'PP2 + speculative' arms, the 'KV transfer' breakdown "
        "segment shows only the portion of KV migration *not* hidden behind scheduler "
        "queueing (usually ~0 by construction); see analysis/ttft_breakdown.csv for the raw "
        "kv_migration_effective_latency_ns-based figures.",
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
        raise RuntimeError("No completed arms found for any workload")
    summary, components, gpu_summary = summarize(pairs)
    paired = pairwise_vs_baseline(summary, ARMS[0])
    paired_vs_pp2 = pairwise_vs_baseline(summary, ARMS[1])
    input8000_redirects = summarize_input8000_redirects(pairs)
    input8000_redirect_breakdown = summarize_input8000_redirect_breakdown(pairs)
    summary.to_csv(ANALYSIS / "comparison_summary.csv", index=False)
    components.to_csv(ANALYSIS / "ttft_breakdown.csv", index=False)
    gpu_summary.to_csv(ANALYSIS / "logical_instance_utilization.csv", index=False)
    paired.to_csv(ANALYSIS / "paired_improvements.csv", index=False)
    paired_vs_pp2.to_csv(ANALYSIS / "paired_improvements_vs_pp2.csv", index=False)
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
    write_report(summary, paired, paired_vs_pp2, missing)
    print(f"Analyzed {len(pairs)} workloads across up to {len(ARMS)} arms")
    print(paired.to_string(index=False))
    print()
    print(paired_vs_pp2.to_string(index=False))
    print(f"Figures: {FIGURES}")
    print(f"Report: {REPORTS / 'interim_pp1_vs_pp2.md'}")


if __name__ == "__main__":
    main()
