#!/usr/bin/env python3
"""Repeat the daily-average trace and build matching PP1/PP2 workloads."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ROOT / "workloads"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repeat600 = load_module("repeat600", Path(__file__).with_name("build_peak5_repeat600.py"))
prepare = load_module("prepare_hongo", Path(__file__).with_name("prepare_hongo_workload.py"))


def main() -> None:
    source = WORKLOADS / "hongo_daily_average_seed1.jsonl"
    pp1_output = WORKLOADS / "hongo_daily_average_repeat600_seed1.jsonl"
    pp2_output = WORKLOADS / "hongo_daily_average_repeat600_seed1_pp2.jsonl"

    repeated = repeat600.repeat(repeat600.load(source))
    pp2_rows = prepare.transform_pp2_rows(repeated, prepare.gpu_positions())
    repeat600.write(pp1_output, repeated)
    repeat600.write(pp2_output, pp2_rows)


if __name__ == "__main__":
    main()
