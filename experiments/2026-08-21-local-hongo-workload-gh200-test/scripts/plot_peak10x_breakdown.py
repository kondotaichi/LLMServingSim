#!/usr/bin/env python3
"""Plot the mean end-to-end TTFT breakdown for the peak 10x runs."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = EXPERIMENT_ROOT / "results"
FIGURES_ROOT = EXPERIMENT_ROOT / "figures"

RUNS = [
    ("1: no_redirect", "peak_10x_repeat600_seed1_1_naive"),
    ("2: redirect_no_kv", "peak_10x_repeat600_seed1_2_redirect_no_kv"),
    ("3: redirect_kv_nopp", "peak_10x_repeat600_seed1_3_redirect_kv_no_pp"),
    ("4: redirect_kv_pp2", "peak_10x_repeat600_seed1_4_redirect_kv_pp2"),
]

COMPONENTS = [
    ("Router queue", "router_capacity_wait_ns", "#c74b3a"),
    ("Scheduler queue", "scheduler_queue_ns", "#e79b37"),
    ("KV transfer", "kv_migration_effective_latency_ns", "#865bd6"),
    ("PP transfer (est.)", "pp_transfer_ns", "#e0459b"),
    ("Compute / prefill", "prefill_service_ns", "#31866f"),
    ("RTT / other comm", "other_communication_ns", "#4f83c2"),
]


def load_run(label: str, run_name: str) -> dict[str, float | int | str]:
    path = RESULTS_ROOT / run_name / "requests.csv"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No requests found in {path}")

    record: dict[str, float | int | str] = {
        "method": label.replace("\n", " "),
        "run": run_name,
        "requests": len(rows),
        "rerouted_requests": sum(int(row["rerouted"]) for row in rows),
    }
    def mean_ns(column: str) -> float:
        return sum(float(row.get(column, 0) or 0) for row in rows) / len(rows)

    router_ns = mean_ns("router_capacity_wait_ns")
    queue_ns = mean_ns("queueing_before_ttft_ns")
    kv_ns = mean_ns("kv_migration_effective_latency_ns")
    communication_ns = mean_ns("communication_latency_ns")
    values_ns = {
        "Router queue": router_ns,
        "Scheduler queue": max(0.0, queue_ns - router_ns),
        "KV transfer": kv_ns,
        "PP transfer (est.)": 0.0,
        "Compute / prefill": mean_ns("prefill_service_ns"),
        "RTT / other comm": max(0.0, communication_ns - kv_ns),
    }
    for component, _, _ in COMPONENTS:
        record[component] = values_ns[component] / 1e6
    record["Mean E2E TTFT"] = (
        sum(float(row["e2e_ttft_ns"]) for row in rows) / len(rows) / 1e6
    )
    record["Unattributed residual"] = record["Mean E2E TTFT"] - sum(
        float(record[component]) for component, _, _ in COMPONENTS
    )
    return record


def write_summary(records: list[dict[str, float | int | str]]) -> None:
    output = FIGURES_ROOT / "peak_10x_ttft_breakdown.csv"
    fieldnames = [
        "method",
        "run",
        "requests",
        "rerouted_requests",
        *(component for component, _, _ in COMPONENTS),
        "Mean E2E TTFT",
        "Unattributed residual",
    ]
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def plot_horizontal(
    records: list[dict[str, float | int | str]], title: str, output_stem: str
) -> None:
    plt.rcParams.update({"font.family": "Arial", "font.size": 16})
    fig_height = 7.56
    fig, axis = plt.subplots(figsize=(15, fig_height), facecolor="#faf8f4")
    axis.set_facecolor("#faf8f4")
    y_positions = list(range(len(records)))
    lefts = [0.0] * len(records)

    for component, _, color in COMPONENTS:
        values = [float(record[component]) for record in records]
        bars = axis.barh(
            y_positions,
            values,
            left=lefts,
            height=0.56,
            label=component,
            color=color,
            edgecolor="#faf8f4",
            linewidth=2,
        )
        for bar, value in zip(bars, values):
            if value >= 4.5:
                axis.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{value:.1f}",
                    ha="center",
                    va="center",
                    fontsize=15,
                    fontweight="bold",
                    color="#fffdf8" if color in {"#c74b3a", "#865bd6", "#31866f", "#4f83c2"} else "#1f1d1a",
                )
        lefts = [left + value for left, value in zip(lefts, values)]

    maximum = max(float(record["Mean E2E TTFT"]) for record in records)
    for y_position, record in zip(y_positions, records):
        total = float(record["Mean E2E TTFT"])
        axis.text(
            total + maximum * 0.018,
            y_position,
            f"{total:.0f} ms",
            ha="left",
            va="center",
            fontsize=20,
            fontweight="bold",
            color="#2b2926",
        )

    axis.set_title(title, fontsize=28, fontweight="bold", pad=24, color="#2b2926")
    axis.set_xlabel("mean E2E TTFT components (ms)", fontsize=20, labelpad=24)
    axis.set_yticks(y_positions, [str(record["method"]) for record in records])
    axis.tick_params(axis="y", labelsize=21, colors="#2b2926", pad=12)
    axis.tick_params(axis="x", labelsize=16, colors="#5f5b55")
    axis.set_xlim(0, maximum * 1.2)
    axis.invert_yaxis()
    axis.grid(axis="x", color="#d9d2c8", linewidth=1)
    axis.set_axisbelow(True)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.legend(
        frameon=False,
        ncols=3,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.20),
        fontsize=18,
        columnspacing=2.6,
        handlelength=1.0,
    )
    fig.subplots_adjust(left=0.22, right=0.91, top=0.84, bottom=0.29)
    for suffix in ("svg", "png", "pdf"):
        fig.savefig(
            FIGURES_ROOT / f"{output_stem}.{suffix}",
            dpi=100,
            facecolor=fig.get_facecolor(),
        )
    plt.close(fig)


def main() -> None:
    FIGURES_ROOT.mkdir(parents=True, exist_ok=True)
    records = [load_run(label, run_name) for label, run_name in RUNS]
    write_summary(records)
    plot_horizontal(
        records,
        "Hongo GH200 10x (600 req, seed1): four-arm TTFT breakdown",
        "peak_10x_ttft_breakdown",
    )
    plot_horizontal(
        records[:1],
        "Hongo GH200 10x (600 req, seed1): no-redirect TTFT breakdown",
        "peak_10x_no_redirect_ttft_breakdown",
    )


if __name__ == "__main__":
    main()
