#!/usr/bin/env python3
"""Plot the four-component TTFT breakdown for the PP2 hotspot sweep."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/pp2_kv_hotspot_sweep_peak5_600"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"

RUNS = [
    ("Hotspot 30%", "Cold", RESULTS / "hot30/pp2_cold/requests.csv"),
    ("Hotspot 30%", "KV migrate", RESULTS / "hot30/pp2_kv/requests.csv"),
    ("Hotspot 40%", "Cold", RESULTS / "hot40/pp2_cold/requests.csv"),
    ("Hotspot 40%", "KV migrate", RESULTS / "hot40/pp2_kv/requests.csv"),
    ("Hotspot 50%", "Cold", RESULTS / "hot50/pp2_cold/requests.csv"),
    ("Hotspot 50%", "KV migrate", RESULTS / "hot50/pp2_kv/requests.csv"),
]

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("Compute / prefill", "#31866f"),
    ("Communication", "#4f83c2"),
]


def mean_ms(rows: list[dict[str, str]], column: str) -> float:
    return sum(float(row[column]) for row in rows) / len(rows) / 1e6


def summarize(rows: list[dict[str, str]]) -> dict[str, float]:
    total = mean_ms(rows, "e2e_ttft_ns")
    scheduler = mean_ms(rows, "queueing_before_ttft_ns")
    compute = mean_ms(rows, "prefill_service_ns")
    communication = mean_ms(rows, "communication_latency_ns")
    router = max(0.0, total - scheduler - compute - communication)
    return {
        "Router queue": router,
        "Scheduler queue": scheduler,
        "Compute / prefill": compute,
        "Communication": communication,
        "Total": total,
    }


def main() -> None:
    summaries = []
    for hotspot, method, path in RUNS:
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 600:
            raise ValueError(f"{path} has {len(rows)} rows; expected 600")
        summaries.append({
            "hotspot": hotspot,
            "method": method,
            "requests": len(rows),
            **summarize(rows),
        })

    ANALYSIS.mkdir(parents=True, exist_ok=True)
    summary_path = ANALYSIS / "pp2_kv_hotspot_sweep_ttft_breakdown.csv"
    with summary_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)

    labels = [
        f'{row["hotspot"]}\n{row["method"]}'
        for row in summaries
    ]
    positions = list(range(len(summaries)))
    left = [0.0] * len(summaries)
    maximum = max(float(row["Total"]) for row in summaries)

    fig, axis = plt.subplots(figsize=(13.5, 7.8))
    for component, color in COMPONENTS:
        widths = [float(row[component]) for row in summaries]
        axis.barh(
            positions,
            widths,
            left=left,
            height=0.68,
            color=color,
            edgecolor="#faf8f4",
            linewidth=2,
            label=component,
        )
        for position, start, width in zip(positions, left, widths):
            if width >= maximum * 0.055:
                axis.text(
                    start + width / 2,
                    position,
                    f"{width:.0f}",
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=9,
                    fontweight="bold",
                )
        left = [start + width for start, width in zip(left, widths)]

    for position, row in zip(positions, summaries):
        total = float(row["Total"])
        axis.text(
            total + maximum * 0.015,
            position,
            f"{total:.0f} ms",
            va="center",
            fontsize=10,
            fontweight="bold",
            color="#262421",
        )

    axis.set_yticks(positions, labels=labels, fontsize=10.5)
    axis.invert_yaxis()
    axis.set_xlim(0, maximum * 1.19)
    axis.set_xlabel("Mean E2E TTFT components (ms)", fontsize=11)
    axis.set_title(
        "PP2 KV migration under spatial hotspots: mean TTFT breakdown",
        fontsize=16,
        pad=14,
    )
    axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=4,
        frameon=False,
        fontsize=10.5,
    )
    fig.tight_layout(rect=(0, 0.07, 1, 1))

    FIGURES.mkdir(parents=True, exist_ok=True)
    png_path = FIGURES / "pp2_kv_hotspot_sweep_ttft_breakdown.png"
    svg_path = FIGURES / "pp2_kv_hotspot_sweep_ttft_breakdown.svg"
    fig.savefig(png_path, dpi=200, bbox_inches="tight", facecolor="#faf8f4")
    fig.savefig(svg_path, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    print(summary_path)
    print(png_path)
    print(svg_path)


if __name__ == "__main__":
    main()
