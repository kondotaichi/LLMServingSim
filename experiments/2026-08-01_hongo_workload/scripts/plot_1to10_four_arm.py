#!/usr/bin/env python3
"""Render the original Hongo 1x-10x results with arms 1-4 only."""

from __future__ import annotations

import importlib.util
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load plotting module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ttft = load_module("hongo_ttft", SCRIPT_DIR / "plot_ttft_breakdown.py")
    tpot = load_module("hongo_tpot", SCRIPT_DIR / "plot_tpot.py")
    ttft.ARMS = ttft.ARMS[:4]

    results_dir = EXP_ROOT / "results"
    analysis_dir = EXP_ROOT / "analysis_1to10_4arm"
    figures_dir = EXP_ROOT / "figures_1to10_4arm"

    for level in range(1, 11):
        prefix = "busy_hour_seed1" if level == 1 else f"peak_{level}x_seed1"
        scenario = f"{level}x"

        ttft_summary, ttft_cdf, notices = ttft.load_available(
            prefix, results_dir, 300
        )
        if notices or len(ttft_summary) != 4:
            details = "; ".join(notices) or f"found {len(ttft_summary)} arms"
            raise RuntimeError(f"Incomplete {scenario} TTFT inputs: {details}")
        ttft.write_csv(
            ttft_summary, analysis_dir / f"{scenario}_ttft_breakdown.csv"
        )
        ttft.write_cdf_csv(
            ttft_cdf, analysis_dir / f"{scenario}_ttft_cdf.csv"
        )
        ttft.render_svg(
            ttft_summary,
            f"Hongo {scenario}: four-arm TTFT breakdown",
            figures_dir / f"{scenario}_ttft_breakdown.svg",
        )
        ttft.render_cdf_svg(
            ttft_cdf,
            f"Hongo {scenario}: four-arm TTFT CDF",
            figures_dir / f"{scenario}_ttft_cdf.svg",
        )

        tpot_summary, tpot_cdf, notices = tpot.load_available(
            prefix, results_dir, 300, 4
        )
        if notices or len(tpot_summary) != 4:
            details = "; ".join(notices) or f"found {len(tpot_summary)} arms"
            raise RuntimeError(f"Incomplete {scenario} TPOT inputs: {details}")
        tpot.write_summary_csv(
            tpot_summary, analysis_dir / f"{scenario}_tpot_summary.csv"
        )
        tpot.write_cdf_csv(
            tpot_cdf, analysis_dir / f"{scenario}_tpot_cdf.csv"
        )
        tpot.render_summary_svg(
            tpot_summary,
            f"Hongo {scenario}: four-arm TPOT summary",
            figures_dir / f"{scenario}_tpot_summary.svg",
        )
        tpot.render_cdf_svg(
            tpot_cdf,
            f"Hongo {scenario}: four-arm TPOT CDF",
            figures_dir / f"{scenario}_tpot_cdf.svg",
        )
        print(f"Rendered {scenario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
