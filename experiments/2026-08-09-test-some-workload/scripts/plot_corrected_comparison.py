#!/usr/bin/env python3
"""Render the capacity-matched hotspot comparison using the shared SVG code."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
PLOT_SOURCE = (
    REPO
    / "experiments/2026-08-01_hongo_workload/scripts/plot_ttft_breakdown.py"
)

ARMS = [
    (
        "1: naive (PP1)",
        ROOT / "results/five_arm_load_12x_hot50_msq32/1_naive/requests.csv",
        1,
    ),
    (
        "2: cold redirect (PP1)",
        ROOT / "results/five_arm_load_12x_hot50_msq32/2_redirect_cold/requests.csv",
        1,
    ),
    (
        "3: KV migrate (PP1)",
        ROOT / "results/five_arm_load_12x_hot50_msq32/3_redirect_kv_pp1/requests.csv",
        1,
    ),
    (
        "4a: cold redirect (PP2)",
        ROOT / "results/pp2_cold_capacity_matched/pp2_cold/requests.csv",
        2,
    ),
    (
        "4b: KV migrate (PP2)",
        ROOT / "results/five_arm_load_12x_hot50_capacity_matched/4_redirect_kv_pp2/requests.csv",
        2,
    ),
    (
        "5: KV migrate + proactive (PP2)",
        ROOT / "results/five_arm_load_12x_hot50_capacity_matched/5_redirect_kv_pp2_proactive/requests.csv",
        2,
    ),
]


def main() -> None:
    spec = importlib.util.spec_from_file_location("shared_ttft_plot", PLOT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {PLOT_SOURCE}")
    plot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plot)

    summaries = []
    cdf_data = {}
    for label, path, pp_size in ARMS:
        rows = plot.load_rows(path)
        summary, ttft = plot.summarize_arm(label, rows, pp_size)
        summaries.append(summary)
        cdf_data[label] = ttft

    analysis = ROOT / "analysis"
    figures = ROOT / "figures"
    plot.write_csv(summaries, analysis / "corrected_hotspot_ttft_breakdown.csv")
    plot.write_cdf_csv(cdf_data, analysis / "corrected_hotspot_ttft_cdf.csv")
    plot.render_svg(
        summaries,
        "Capacity-matched hotspot: TTFT breakdown",
        figures / "corrected_hotspot_ttft_breakdown.svg",
    )
    plot.render_cdf_svg(
        cdf_data,
        "Capacity-matched hotspot: TTFT CDF",
        figures / "corrected_hotspot_ttft_cdf.svg",
    )


if __name__ == "__main__":
    main()
