#!/usr/bin/env python3
"""Run the shared formula-model comparison for the input10000 experiment."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED = (
    ROOT.parent
    / "2026-07-14_prompt6000_90s_three_policy_良結果"
    / "scripts/analyze_formula_phase1_model.py"
)

spec = importlib.util.spec_from_file_location("formula_phase1_analysis", SHARED)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
analysis.ROOT = ROOT
analysis.RESULTS = ROOT / "results"
analysis.ANALYSIS = ROOT / "analysis/formula_phase1_model"
analysis.FIGURES = ROOT / "figures/formula_phase1_model"
analysis.REPORT = ROOT / "reports/06_formula_phase1_model_analysis.md"
analysis.WORKLOAD_LABEL = "Input 10000 / reuse 50% / 180s"


if __name__ == "__main__":
    analysis.main()
