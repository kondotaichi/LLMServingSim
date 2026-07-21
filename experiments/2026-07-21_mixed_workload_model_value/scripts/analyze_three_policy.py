#!/usr/bin/env python3
"""Analyze the completed mixed-workload three-policy experiment."""

import json
import re
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
POLICIES = {
    "KV migrate": "NEAREST_MIGRATE_KV",
    "Multi no model": "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE",
    "Multi learned": "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE",
}
COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def load_frames():
    frames = {}
    for condition_dir in sorted(RESULTS.iterdir()):
        if not condition_dir.is_dir():
            continue
        condition = condition_dir.name
        match = re.fullmatch(r"mixed_rate(\d+p?\d*)_seed(\d+)", condition)
        if match is None:
            continue
        rate = float(match.group(1).replace("p", "."))
        seed = int(match.group(2))
        workload_path = ROOT / "workloads" / f"{condition}.jsonl"
        workload = pd.DataFrame(
            json.loads(line) for line in workload_path.open(encoding="utf-8")
        ).set_index("request_id")
        workload_columns = ["mixed_traffic_phase", "mixed_reuse_ratio"]
        for label, policy in POLICIES.items():
            path = condition_dir / policy / "requests.csv"
            frame = pd.read_csv(path).set_index("request id").sort_index()
            frame = frame.join(workload[workload_columns])
            frame["ttft_ms"] = frame.e2e_ttft_ns / 1e6
            frame["rate"] = rate
            frame["seed"] = seed
            frame["condition"] = condition
            frame["policy"] = label
            frames[condition, label] = frame
    return frames


def metric_row(frame, **labels):
    ttft = frame.ttft_ms
    return {
        **labels,
        "requests": len(frame),
        "mean_ms": ttft.mean(),
        "p50_ms": ttft.quantile(0.50),
        "p95_ms": ttft.quantile(0.95),
        "p99_ms": ttft.quantile(0.99),
        "max_ms": ttft.max(),
        "redirects": int(frame.rerouted.sum()),
        "router_wait_mean_ms": frame.router_capacity_wait_ns.mean() / 1e6,
        "scheduler_queue_mean_ms": frame.queueing_before_ttft_ns.mean() / 1e6,
        "prefill_mean_ms": frame.prefill_service_ns.mean() / 1e6,
        "completion_mean_ms": frame.request_completion_latency_ns.mean() / 1e6,
    }


def make_tables(frames):
    run_rows = []
    for (condition, policy), frame in frames.items():
        run_rows.append(metric_row(
            frame,
            condition=condition,
            rate=frame.rate.iloc[0],
            seed=frame.seed.iloc[0],
            policy=policy,
        ))
    run_summary = pd.DataFrame(run_rows).sort_values(
        ["rate", "seed", "policy"]
    )

    pooled_rows = []
    subgroup_rows = []
    for rate in sorted(run_summary.rate.unique()):
        for policy in POLICIES:
            selected = [
                frame for (_, label), frame in frames.items()
                if label == policy and frame.rate.iloc[0] == rate
            ]
            pooled = pd.concat(selected)
            pooled_rows.append(metric_row(
                pooled, rate=rate, policy=policy
            ))
            for dimension, values in (
                ("phase", ("normal", "burst")),
                ("input", (512, 2000, 4000, 6000, 8000, 10000)),
                ("reuse", (0.0, 0.25, 0.5)),
            ):
                column = {
                    "phase": "mixed_traffic_phase",
                    "input": "input",
                    "reuse": "mixed_reuse_ratio",
                }[dimension]
                for value in values:
                    group = pooled[pooled[column].eq(value)]
                    subgroup_rows.append(metric_row(
                        group,
                        rate=rate,
                        policy=policy,
                        dimension=dimension,
                        value=value,
                    ))

    paired_rows = []
    for condition in sorted({condition for condition, _ in frames}):
        no_model = frames[condition, "Multi no model"]
        learned = frames[condition, "Multi learned"]
        delta = learned.ttft_ms - no_model.ttft_ms
        paired_rows.append({
            "condition": condition,
            "rate": learned.rate.iloc[0],
            "seed": learned.seed.iloc[0],
            "learned_minus_no_model_mean_ms": delta.mean(),
            "learned_better_requests": int(delta.lt(-0.001).sum()),
            "learned_worse_requests": int(delta.gt(0.001).sum()),
            "same_requests": int(delta.abs().le(0.001).sum()),
            "different_gpu": int(learned.gpu_id.ne(no_model.gpu_id).sum()),
            "no_model_redirects": int(no_model.rerouted.sum()),
            "learned_redirects": int(learned.rerouted.sum()),
        })
    return (
        run_summary,
        pd.DataFrame(pooled_rows),
        pd.DataFrame(subgroup_rows),
        pd.DataFrame(paired_rows),
    )


def prediction_summary(frames):
    learned = pd.concat([
        frame for (_, policy), frame in frames.items()
        if policy == "Multi learned"
    ])
    redirected = learned[learned.rerouted.eq(1)].copy()
    error = (
        redirected.e2e_ttft_ns
        - redirected.oneshot_predicted_redirect_ttft_ns
    ) / 1e6
    return pd.DataFrame([{
        "redirected_predictions": len(error),
        "mae_ms": error.abs().mean(),
        "bias_ms": error.mean(),
        "median_error_ms": error.median(),
        "p90_abs_error_ms": error.abs().quantile(0.90),
        "p95_abs_error_ms": error.abs().quantile(0.95),
        "p99_abs_error_ms": error.abs().quantile(0.99),
    }])


def ttft_breakdown(frame):
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


def make_ttft_breakdown_figures(frames):
    rows = []
    rates = sorted({frame.rate.iloc[0] for frame in frames.values()})
    populations = (
        ("All requests", None),
        ("Redirected only", 1),
        ("Not redirected only", 0),
    )
    for rate in rates:
        pooled = {
            policy: pd.concat([
                frame for (_, label), frame in frames.items()
                if label == policy and frame.rate.iloc[0] == rate
            ])
            for policy in POLICIES
        }
        breakdowns = {}
        for population, rerouted in populations:
            for policy, frame in pooled.items():
                selected = frame if rerouted is None else frame[frame.rerouted.eq(rerouted)]
                if selected.empty:
                    continue
                values = ttft_breakdown(selected)
                breakdowns[population, policy] = values
                rows.append({
                    "rate": rate,
                    "population": population,
                    "policy": policy,
                    "request_count": len(selected),
                    **values,
                })

        maximum = max(values["Total"] for values in breakdowns.values())
        fig, axes = plt.subplots(1, 3, figsize=(21, 7.5), sharex=True)
        positions = np.arange(len(POLICIES))
        for axis, (population, _) in zip(axes, populations):
            left = np.zeros(len(POLICIES))
            for component, color in COMPONENTS:
                widths = np.array([
                    breakdowns.get((population, policy), {}).get(component, 0.0)
                    for policy in POLICIES
                ])
                axis.barh(
                    positions, widths, left=left, height=0.58, color=color,
                    edgecolor="#faf8f4", linewidth=2, label=component,
                )
                left += widths
            for position, policy in enumerate(POLICIES):
                key = population, policy
                if key not in breakdowns:
                    axis.text(maximum * 0.02, position, "no requests", va="center",
                              color="#77736d")
                    continue
                total = breakdowns[key]["Total"]
                count = len(pooled[policy]) if population == "All requests" else int(
                    pooled[policy].rerouted.eq(1 if population == "Redirected only" else 0).sum()
                )
                axis.text(total + maximum * 0.015, position,
                          f"{total:.0f} ms\n(n={count})", va="center",
                          fontsize=9, fontweight="bold")
            axis.set_yticks(positions, list(POLICIES))
            axis.invert_yaxis()
            axis.set_xlim(0, maximum * 1.24)
            axis.set_title(population)
            axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
            axis.set_axisbelow(True)
            axis.spines[["top", "right", "left"]].set_visible(False)
        axes[1].set_xlabel("mean E2E TTFT components (ms)")
        fig.suptitle(
            f"Mixed workload / {rate:g} rps / 3 seeds: mean E2E TTFT breakdown by redirect status",
            fontsize=18,
        )
        handles, labels = axes[-1].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS),
                   frameon=False)
        fig.tight_layout(rect=(0, 0.07, 1, 0.94))
        rate_label = str(rate).replace(".", "p")
        fig.savefig(
            FIGURES / f"ttft_breakdown_rate{rate_label}.png", dpi=180,
            bbox_inches="tight", facecolor="#faf8f4",
        )
        plt.close(fig)
    pd.DataFrame(rows).to_csv(
        ANALYSIS / "ttft_breakdown_by_redirect_status.csv", index=False
    )


def make_figures(run_summary, pooled_summary, subgroup_summary):
    FIGURES.mkdir(parents=True, exist_ok=True)
    colors = {
        "KV migrate": "#4c78a8",
        "Multi no model": "#f58518",
        "Multi learned": "#2f8a62",
    }
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    for ax, metric, title in zip(
        axes,
        ("mean_ms", "p95_ms", "p99_ms"),
        ("Mean TTFT", "p95 TTFT", "p99 TTFT"),
    ):
        pivot = pooled_summary.pivot(index="rate", columns="policy", values=metric)
        pivot[list(POLICIES)].plot.bar(
            ax=ax, color=[colors[label] for label in POLICIES]
        )
        ax.set_title(title)
        ax.set_xlabel("Average request rate (rps)")
        ax.set_ylabel("ms")
        ax.tick_params(axis="x", rotation=0)
        if ax is not axes[-1]:
            ax.get_legend().remove()
    axes[-1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "rate_level_performance.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for rate, ax in zip(sorted(run_summary.rate.unique()), axes):
        selected = run_summary[run_summary.rate.eq(rate)]
        for policy in POLICIES:
            group = selected[selected.policy.eq(policy)].sort_values("seed")
            ax.plot(group.seed, group.mean_ms, marker="o", linewidth=2,
                    label=policy, color=colors[policy])
        ax.set_title(f"Mean TTFT across seeds: {rate:g} rps")
        ax.set_xlabel("Seed")
        ax.set_ylabel("Mean TTFT (ms)")
        ax.set_xticks([1, 2, 3])
    axes[-1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "seed_consistency.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    input_groups = subgroup_summary[
        subgroup_summary.dimension.eq("input")
    ]
    learned = input_groups[input_groups.policy.eq("Multi learned")].pivot(
        index="rate", columns="value", values="mean_ms"
    )
    no_model = input_groups[input_groups.policy.eq("Multi no model")].pivot(
        index="rate", columns="value", values="mean_ms"
    )
    delta = learned - no_model
    fig, ax = plt.subplots(figsize=(9, 3.8))
    limit = max(abs(delta.min().min()), abs(delta.max().max()))
    image = ax.imshow(delta, cmap="RdYlGn_r", vmin=-limit, vmax=limit,
                      aspect="auto")
    ax.set_xticks(range(len(delta.columns)), [int(value) for value in delta.columns])
    ax.set_yticks(range(len(delta.index)), [f"{value:g}" for value in delta.index])
    ax.set_xlabel("Input tokens")
    ax.set_ylabel("Average request rate (rps)")
    ax.set_title("Learned minus no-model Mean TTFT")
    for row in range(len(delta.index)):
        for column in range(len(delta.columns)):
            ax.text(column, row, f"{delta.iloc[row, column]:+.1f}",
                    ha="center", va="center")
    fig.colorbar(image, ax=ax, label="TTFT delta (ms; negative is learned better)")
    fig.tight_layout()
    fig.savefig(FIGURES / "learned_value_by_input.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    frames = load_frames()
    run_summary, pooled_summary, subgroup_summary, paired_summary = make_tables(
        frames
    )
    predictions = prediction_summary(frames)
    run_summary.to_csv(ANALYSIS / "run_summary.csv", index=False)
    pooled_summary.to_csv(ANALYSIS / "pooled_rate_summary.csv", index=False)
    subgroup_summary.to_csv(ANALYSIS / "subgroup_summary.csv", index=False)
    paired_summary.to_csv(ANALYSIS / "learned_vs_no_model_paired.csv", index=False)
    predictions.to_csv(ANALYSIS / "learned_redirect_prediction_accuracy.csv", index=False)
    make_figures(run_summary, pooled_summary, subgroup_summary)
    make_ttft_breakdown_figures(frames)
    print(pooled_summary.to_string(index=False))
    print(predictions.to_string(index=False))


if __name__ == "__main__":
    main()
