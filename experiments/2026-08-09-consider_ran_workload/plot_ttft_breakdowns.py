#!/usr/bin/env python3
"""Render the 1x-10x TTFT breakdowns for the peak-RAN experiment."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXP_ROOT.parents[1]
BASE_PLOT = (
    REPO_ROOT
    / "experiments/2026-08-01_hongo_workload/scripts/plot_ttft_breakdown.py"
)
BASE_TPOT_PLOT = (
    REPO_ROOT
    / "experiments/2026-08-01_hongo_workload/scripts/plot_tpot.py"
)


def load_plot_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load plotting module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def tpot_summary(plot, results_dir: Path, prefix: str) -> list[dict]:
    summary = []
    for label, suffix, _ in plot.ARMS:
        path = results_dir / f"{prefix}_{suffix}" / "requests.csv"
        rows = plot.load_rows(path)
        if len(rows) != 300:
            raise RuntimeError(f"Expected 300 rows in {path}, found {len(rows)}")
        queue_ms = []
        active_ms = []
        total_ms = []
        for row in rows:
            decode_tokens = max(0, int(row["output"]) - 1)
            if decode_tokens == 0:
                queue_ms.append(0.0)
                active_ms.append(0.0)
                total_ms.append(0.0)
                continue
            queue_ms.append(float(row["decode_queueing_ns"]) / decode_tokens / 1e6)
            active_ms.append(float(row["decode_active_ns"]) / decode_tokens / 1e6)
            total_ms.append(float(row["TPOT"]) / 1e6)
        summary.append({
            "arm": label,
            "requests": len(rows),
            "Decode queue": mean(queue_ms),
            "Decode active": mean(active_ms),
            "Total": mean(total_ms),
        })
    return summary


def write_tpot_csv(summary: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("arm", "requests", "Decode queue", "Decode active", "Total"),
        )
        writer.writeheader()
        writer.writerows(summary)


def main() -> int:
    plot = load_plot_module("hongo_ttft_plot", BASE_PLOT)
    tpot_plot = load_plot_module("hongo_tpot_plot", BASE_TPOT_PLOT)
    plot.ARMS = [
        ("1: no_redirect", "1_no_redirect", 1),
        ("2: redirect_no_kv", "2_redirect_no_kv", 1),
        ("3: redirect_kv_pp1", "3_redirect_kv_pp1", 1),
        ("4: redirect_kv_pp2", "4_redirect_kv_pp2", 2),
    ]
    tpot_plot.ARMS = [
        ("1: no_redirect", "1_no_redirect", "#c74b3a"),
        ("2: redirect_no_kv", "2_redirect_no_kv", "#e79b37"),
        ("3: redirect_kv_pp1", "3_redirect_kv_pp1", "#865bd6"),
        ("4: redirect_kv_pp2", "4_redirect_kv_pp2", "#e0459b"),
    ]
    results_dir = EXP_ROOT / "results_peak_ran_12ru"
    analysis_dir = EXP_ROOT / "analysis_peak_ran_12ru"
    figures_dir = EXP_ROOT / "figures_peak_ran_12ru"

    for level in range(1, 11):
        prefix = f"{level}x_seed1"
        summary, ttft_cdf, notices = plot.load_available(prefix, results_dir, 300)
        if notices or len(summary) != len(plot.ARMS):
            details = "; ".join(notices) or f"found {len(summary)} arms"
            raise RuntimeError(f"Incomplete {level}x inputs: {details}")
        plot.write_csv(
            summary, analysis_dir / f"{level}x_ttft_breakdown.csv"
        )
        plot.write_cdf_csv(
            ttft_cdf, analysis_dir / f"{level}x_ttft_cdf.csv"
        )
        plot.COMPONENTS = [
            ("Router queue", "#c74b3a"),
            ("Scheduler queue", "#e79b37"),
            ("KV transfer", "#865bd6"),
            ("PP transfer (est.)", "#e0459b"),
            ("Compute / prefill", "#31866f"),
            ("RTT / other comm", "#4f83c2"),
        ]
        plot.render_svg(
            summary,
            f"Hongo {level}x, peak RAN VRAM reservation: TTFT breakdown",
            figures_dir / f"{level}x_ttft_breakdown.svg",
        )
        plot.render_cdf_svg(
            ttft_cdf,
            f"Hongo {level}x, peak RAN VRAM reservation: TTFT CDF",
            figures_dir / f"{level}x_ttft_cdf.svg",
        )
        tpot = tpot_summary(plot, results_dir, prefix)
        write_tpot_csv(tpot, analysis_dir / f"{level}x_tpot_breakdown.csv")
        plot.COMPONENTS = [
            ("Decode queue", "#e79b37"),
            ("Decode active", "#31866f"),
        ]
        plot.render_svg(
            tpot,
            f"Hongo {level}x, peak RAN VRAM reservation: TPOT breakdown",
            figures_dir / f"{level}x_tpot_breakdown.svg",
            axis_label="mean TPOT components (ms/token)",
        )
        _, tpot_cdf, notices = tpot_plot.load_available(
            prefix, results_dir, 300, 4
        )
        if notices or len(tpot_cdf) != 4:
            details = "; ".join(notices) or f"found {len(tpot_cdf)} arms"
            raise RuntimeError(f"Incomplete {level}x TPOT inputs: {details}")
        tpot_plot.write_cdf_csv(
            tpot_cdf, analysis_dir / f"{level}x_tpot_cdf.csv"
        )
        tpot_plot.render_cdf_svg(
            tpot_cdf,
            f"Hongo {level}x, peak RAN VRAM reservation: TPOT CDF",
            figures_dir / f"{level}x_tpot_cdf.svg",
        )
        print(f"Rendered {level}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
