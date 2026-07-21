#!/usr/bin/env python3
"""Run the shared all-policy analysis for the input10000 workload."""

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED = (
    ROOT.parent
    / "2026-07-14_prompt6000_90s_three_policy_良結果"
    / "scripts/analyze_all_policies.py"
)

sys.argv = [
    str(SHARED),
    "--root", str(ROOT),
    "--workload-label", "input10000 / reuse0.5 / 180s",
]
runpy.run_path(str(SHARED), run_name="__main__")
