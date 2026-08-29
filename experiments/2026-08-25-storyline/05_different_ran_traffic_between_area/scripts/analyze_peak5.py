#!/usr/bin/env python3
import importlib.util
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
ANALYZER_PATH = (
    EXP_ROOT.parent / "04_proposed_method/scripts/analyze_peak5.py"
)

spec = importlib.util.spec_from_file_location("storyline04_analyze_peak5", ANALYZER_PATH)
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)

analyzer.EXP_ROOT = EXP_ROOT
analyzer.ANALYSIS_ROOT = EXP_ROOT / "analysis"
analyzer.FIGURE_ROOT = EXP_ROOT / "figures"
analyzer.RUNS = (
    ("local_only", "Local only", EXP_ROOT / "results/peak_5x_local_only_tokyo23_kagoshima17/requests.csv"),
    ("redirect_cold", "Naive cold redirect", EXP_ROOT / "results/peak_5x_redirect_cold_tokyo23_kagoshima17/requests.csv"),
    ("kv_redirect", "KV redirect", EXP_ROOT / "results/peak_5x_kv_redirect_tokyo23_kagoshima17/requests.csv"),
    ("pp2", "PP=2", EXP_ROOT / "results/peak_5x_pp_only_tokyo23_kagoshima17/requests.csv"),
    ("proposed", "KV redirect + PP=2", EXP_ROOT / "results/peak_5x_proposed_tokyo23_kagoshima17/requests.csv"),
)
analyzer.CONDITION_LABEL = (
    "APN, Active TCP Tokyo 23% / Kagoshima 17%, AI demand 70/30"
)


if __name__ == "__main__":
    analyzer.main()
