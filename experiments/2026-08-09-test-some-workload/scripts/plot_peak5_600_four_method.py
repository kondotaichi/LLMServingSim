#!/usr/bin/env python3
"""Render TTFT breakdown and CDF for the 600-request Peak 5x run."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
RESULTS = ROOT / "results/peak_5x_600_four_method"
PLOT_SOURCE = (
    REPO
    / "experiments/2026-08-01_hongo_workload/scripts/plot_ttft_breakdown.py"
)

ARMS = [
    ("1: no_redirect", RESULTS / "1_naive/requests.csv", 1),
    ("2: redirect_no_kv", RESULTS / "2_redirect_cold/requests.csv", 1),
    ("3: redirect_kv_nopp", RESULTS / "3_redirect_kv_pp1/requests.csv", 1),
    ("4: redirect_kv_pp2", RESULTS / "4_redirect_kv_pp2/requests.csv", 2),
]

DISPLAY_NAMES = {
    "1: no_redirect": "Naive (PP1)",
    "2: redirect_no_kv": "Cold redirect (PP1)",
    "3: redirect_kv_nopp": "KV migrate (PP1)",
    "4: redirect_kv_pp2": "KV migrate (PP2)",
}

LINE_COLORS = {
    "1: no_redirect": "#2f5f9f",
    "2: redirect_no_kv": "#d18120",
    "3: redirect_kv_nopp": "#31866f",
    "4: redirect_kv_pp2": "#865bd6",
}

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("PP transfer (est.)", "#e0459b"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def split_rows(rows: list[dict[str, str]], subset: str) -> list[dict[str, str]]:
    if subset == "all":
        return rows
    rerouted = 1 if subset == "redirected" else 0
    return [row for row in rows if int(float(row.get("rerouted", "0") or 0)) == rerouted]


def render_breakdown_png(plot, rows_by_arm, pp_by_arm, output: Path) -> None:
    subsets = [
        ("All requests", "all"),
        ("Redirected only", "redirected"),
        ("Not redirected only", "not_redirected"),
    ]
    breakdowns = {}
    maximum = 0.0
    for _, subset in subsets:
        for arm, rows in rows_by_arm.items():
            selected = split_rows(rows, subset)
            if selected:
                values = plot.breakdown(selected, pp_by_arm[arm])
                breakdowns[(subset, arm)] = (values, len(selected))
                maximum = max(maximum, float(values["Total"]))

    fig, axes = plt.subplots(1, 3, figsize=(23, 8.5), sharex=True)
    positions = list(range(len(ARMS)))
    for axis, (panel_title, subset) in zip(axes, subsets):
        left = [0.0] * len(ARMS)
        for component, color in COMPONENTS:
            widths = []
            for arm, _, _ in ARMS:
                item = breakdowns.get((subset, arm))
                widths.append(float(item[0][component]) if item else 0.0)
            axis.barh(
                positions,
                widths,
                left=left,
                height=0.58,
                color=color,
                edgecolor="#faf8f4",
                linewidth=2,
                label=component,
            )
            left = [old + width for old, width in zip(left, widths)]

        for position, (arm, _, _) in zip(positions, ARMS):
            item = breakdowns.get((subset, arm))
            if item is None:
                axis.text(
                    maximum * 0.02,
                    position,
                    "no requests",
                    va="center",
                    fontsize=10,
                    color="#8a857d",
                )
                continue
            values, count = item
            total = float(values["Total"])
            axis.text(
                total + maximum * 0.012,
                position,
                f"{total:.0f} ms\n(n={count})",
                va="center",
                fontsize=9.5,
                fontweight="bold",
                color="#262421",
            )

        axis.set_title(panel_title, fontsize=15, pad=12)
        axis.set_yticks(
            positions,
            labels=[DISPLAY_NAMES[arm] for arm, _, _ in ARMS],
            fontsize=10.5,
        )
        axis.invert_yaxis()
        axis.set_xlim(0, maximum * 1.23)
        axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=10.5)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=6,
        frameon=False,
        fontsize=10.5,
        bbox_to_anchor=(0.5, 0.015),
    )
    fig.suptitle(
        "Hongo Peak 5x (600 requests): mean E2E TTFT breakdown by redirect status",
        fontsize=19,
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0.07, 1, 0.94))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def render_cdf_png(rows_by_arm, output: Path) -> None:
    subsets = [
        ("All requests", "all"),
        ("Redirected only", "redirected"),
        ("Not redirected only", "not_redirected"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(23, 7.2), sharex=True, sharey=True)
    for axis, (panel_title, subset) in zip(axes, subsets):
        for arm, _, _ in ARMS:
            selected = split_rows(rows_by_arm[arm], subset)
            if not selected:
                continue
            values = sorted(float(row["e2e_ttft_ns"]) / 1e6 for row in selected)
            probabilities = [(index + 1) / len(values) for index in range(len(values))]
            axis.step(
                values,
                probabilities,
                where="post",
                linewidth=2.3,
                color=LINE_COLORS[arm],
                label=f"{DISPLAY_NAMES[arm]} (n={len(values)})",
            )
        axis.set_xscale("log")
        axis.set_ylim(0, 1.02)
        axis.set_xlabel("E2E TTFT (ms, log scale)", fontsize=10.5)
        axis.set_title(panel_title, fontsize=15, pad=12)
        axis.grid(True, which="both", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(loc="lower right", fontsize=9)
    axes[0].set_ylabel("cumulative probability", fontsize=11)
    fig.suptitle(
        "Hongo Peak 5x (600 requests): E2E TTFT CDF by redirect status",
        fontsize=19,
        y=0.98,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main() -> None:
    spec = importlib.util.spec_from_file_location("shared_ttft_plot", PLOT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {PLOT_SOURCE}")
    plot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plot)

    summaries = []
    cdf_data = {}
    rows_by_arm = {}
    pp_by_arm = {}
    for label, path, pp_size in ARMS:
        rows = plot.load_rows(path)
        if len(rows) != 600:
            raise ValueError(f"{path} has {len(rows)} requests, expected 600")
        summary, ttft = plot.summarize_arm(label, rows, pp_size)
        summaries.append(summary)
        cdf_data[label] = ttft
        rows_by_arm[label] = rows
        pp_by_arm[label] = pp_size

    analysis = ROOT / "analysis"
    figures = ROOT / "figures"
    plot.write_csv(summaries, analysis / "peak_5x_600_ttft_breakdown.csv")
    plot.write_cdf_csv(cdf_data, analysis / "peak_5x_600_ttft_cdf.csv")
    plot.render_svg(
        summaries,
        "Hongo Peak 5x (600 requests): TTFT breakdown",
        figures / "peak_5x_600_ttft_breakdown.svg",
    )
    plot.render_cdf_svg(
        cdf_data,
        "Hongo Peak 5x (600 requests): TTFT CDF",
        figures / "peak_5x_600_ttft_cdf.svg",
    )
    render_breakdown_png(
        plot,
        rows_by_arm,
        pp_by_arm,
        figures / "peak_5x_600_ttft_breakdown.png",
    )
    render_cdf_png(
        rows_by_arm,
        figures / "peak_5x_600_ttft_cdf.png",
    )

    for row in summaries:
        print(
            f'{row["arm"]}: mean={float(row["mean_ttft_ms"]):.3f} ms, '
            f'p50={float(row["p50_ttft_ms"]):.3f} ms, '
            f'p95={float(row["p95_ttft_ms"]):.3f} ms, '
            f'p99={float(row["p99_ttft_ms"]):.3f} ms, '
            f'redirects={int(row["redirects"])}'
        )


if __name__ == "__main__":
    main()
