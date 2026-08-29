#!/usr/bin/env python3
"""Plot Peak 5x TTFT and estimated electricity-cost comparisons."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT.parent
RESULTS = ROOT / "results/peak_5x_600_four_method"
FIGURES = ROOT / "figures"
GEO_SUMMARY = (
    EXPERIMENTS
    / "2026-08-02-kagosima-tokyo/analysis/hongo_1x10x/summary.csv"
)

IDLE_W = 50.0
TDP_W = 450.0
TOKYO_YEN_PER_KWH = 23.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def mean_ttft_ms(path: Path) -> float:
    rows = read_csv(path)
    return sum(float(row["e2e_ttft_ns"]) for row in rows) / len(rows) / 1e6


def energy_wh(path: Path, pp_size: int) -> float:
    """Integrate PP=1 directly and project PP=2 across both pipeline stages."""
    joules = 0.0
    for row in read_csv(path):
        gpu_id = int(row["gpu_id"])
        if pp_size == 2 and gpu_id >= 6:
            continue
        multiplier = 2 if pp_size == 2 else 1
        duration_ns = float(row["window_duration_ns"])
        busy_ns = float(row["busy_time_ns"])
        joules += multiplier * (
            IDLE_W * duration_ns + (TDP_W - IDLE_W) * busy_ns
        ) / 1e9
    return joules / 3600


def add_value_labels(axis, bars, fmt: str) -> None:
    maximum = max(bar.get_height() for bar in bars)
    for bar in bars:
        value = bar.get_height()
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            value + maximum * 0.025,
            fmt.format(value),
            ha="center",
            va="bottom",
            fontsize=12,
            fontweight="bold",
            color="#292724",
        )


def render_comparison(
    labels: list[str],
    ttft_ms: list[float],
    cost_yen: list[float],
    colors: list[str],
    title: str,
    subtitle: str,
    output: Path,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.2))
    x = range(len(labels))

    ttft_bars = axes[0].bar(x, ttft_ms, color=colors, width=0.62)
    axes[0].set_xticks(list(x), labels=labels, fontsize=11)
    axes[0].set_ylabel("Mean E2E TTFT (ms)", fontsize=11)
    axes[0].set_title("Latency", fontsize=15, pad=12)
    axes[0].set_ylim(0, max(ttft_ms) * 1.22)
    add_value_labels(axes[0], ttft_bars, "{:.0f} ms")

    cost_bars = axes[1].bar(x, cost_yen, color=colors, width=0.62)
    axes[1].set_xticks(list(x), labels=labels, fontsize=11)
    axes[1].set_ylabel("Estimated electricity cost (yen)", fontsize=11)
    axes[1].set_title("Electricity cost", fontsize=15, pad=12)
    axes[1].set_ylim(0, max(cost_yen) * 1.22)
    add_value_labels(axes[1], cost_bars, "{:.3f} yen")

    for axis in axes:
        axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)

    ttft_delta = (ttft_ms[1] / ttft_ms[0] - 1) * 100
    cost_delta = (cost_yen[1] / cost_yen[0] - 1) * 100
    axes[0].text(
        0.98,
        0.94,
        f"Change: {ttft_delta:+.1f}%",
        transform=axes[0].transAxes,
        ha="right",
        va="top",
        fontsize=12,
        color="#3d3935",
    )
    axes[1].text(
        0.98,
        0.94,
        f"Change: {cost_delta:+.1f}%",
        transform=axes[1].transAxes,
        ha="right",
        va="top",
        fontsize=12,
        color="#3d3935",
    )

    fig.suptitle(title, fontsize=18, y=0.99)
    fig.text(0.5, 0.925, subtitle, ha="center", fontsize=10.5, color="#625d57")
    fig.tight_layout(rect=(0, 0.02, 1, 0.89), w_pad=4)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main() -> None:
    baseline_dir = RESULTS / "1_naive"
    proposed_dir = RESULTS / "4_redirect_kv_pp2"
    proposal_ttft = [
        mean_ttft_ms(baseline_dir / "requests.csv"),
        mean_ttft_ms(proposed_dir / "requests.csv"),
    ]
    proposal_energy = [
        energy_wh(baseline_dir / "gpu_utilization_timeseries.csv", 1),
        energy_wh(proposed_dir / "gpu_utilization_timeseries.csv", 2),
    ]
    proposal_cost = [
        value / 1000 * TOKYO_YEN_PER_KWH for value in proposal_energy
    ]
    render_comparison(
        ["Without proposal\nNaive (PP1)", "With proposal\nRedirect + KV + PP2"],
        proposal_ttft,
        proposal_cost,
        ["#7f8c8d", "#31866f"],
        "Peak 5x: proposed method trade-off",
        "600 requests; GPU-only estimate; Tokyo electricity rate = 23.0 yen/kWh",
        FIGURES / "peak_5x_proposal_ttft_electricity_cost.png",
    )

    geo_rows = [
        row
        for row in read_csv(GEO_SUMMARY)
        if int(row["load_multiplier"]) == 5
    ]
    geo_by_arm = {row["arm"]: row for row in geo_rows}
    all_tokyo = geo_by_arm["all_tokyo_baseline"]
    mixed = geo_by_arm["kg_baseline"]
    render_comparison(
        ["Tokyo only", "Tokyo + Kagoshima"],
        [float(all_tokyo["mean_ttft_ms"]), float(mixed["mean_ttft_ms"])],
        [float(all_tokyo["projected_cost_yen"]), float(mixed["projected_cost_yen"])],
        ["#2f5f9f", "#d18120"],
        "Peak 5x: geographic placement trade-off",
        "300 requests; PP2; Tokyo = 23.0 and Kagoshima = 14.7 yen/kWh",
        FIGURES / "peak_5x_tokyo_kagoshima_ttft_electricity_cost.png",
    )

    print(
        "proposal:",
        proposal_ttft,
        proposal_energy,
        proposal_cost,
    )
    print(
        "geography:",
        [all_tokyo["mean_ttft_ms"], mixed["mean_ttft_ms"]],
        [all_tokyo["projected_energy_wh"], mixed["projected_energy_wh"]],
        [all_tokyo["projected_cost_yen"], mixed["projected_cost_yen"]],
    )


if __name__ == "__main__":
    main()
