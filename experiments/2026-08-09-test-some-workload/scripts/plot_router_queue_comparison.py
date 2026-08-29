#!/usr/bin/env python3
"""Render the two-wave PP2 router-queue comparison."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
RESULTS = ROOT / "results/five_arm_pp2_router_queue_two_wave_msq1"
PLOT_SOURCE = (
    REPO
    / "experiments/2026-08-01_hongo_workload/scripts/plot_ttft_breakdown.py"
)

ARMS = [
    ("1: naive (PP1)", RESULTS / "1_naive/requests.csv", 1),
    ("2: cold redirect (PP1)", RESULTS / "2_redirect_cold/requests.csv", 1),
    ("3: KV migrate (PP1)", RESULTS / "3_redirect_kv_pp1/requests.csv", 1),
    (
        "4: KV migrate (PP2)",
        ROOT / "results/pp2_router_queue_two_wave_msq1/pp2_kv/requests.csv",
        2,
    ),
    (
        "5: KV migrate + proactive (PP2)",
        RESULTS / "5_redirect_kv_pp2_proactive/requests.csv",
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
    for label, path, pp_size in ARMS:
        rows = plot.load_rows(path)
        summary, _ = plot.summarize_arm(label, rows, pp_size)
        summaries.append(summary)

    plot.write_csv(summaries, RESULTS / "ttft_breakdown.csv")
    plot.render_svg(
        summaries,
        "Two-wave capacity saturation: TTFT breakdown",
        RESULTS / "ttft_breakdown.svg",
    )


if __name__ == "__main__":
    main()
