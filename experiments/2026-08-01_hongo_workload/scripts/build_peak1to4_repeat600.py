#!/usr/bin/env python3
"""Repeat the original Peak 1x-4x traces for sustained-load validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("build_peak5_repeat600.py")
SPEC = importlib.util.spec_from_file_location("repeat600", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT}")
repeat600 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repeat600)

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ROOT / "workloads"
LEVELS = {
    1: "busy_hour",
    2: "peak_2x",
    3: "peak_3x",
    4: "peak_4x",
}


def main() -> None:
    for level, source_name in LEVELS.items():
        sources = {
            "pp1": WORKLOADS / f"hongo_{source_name}_seed1.jsonl",
            "pp2": WORKLOADS / f"hongo_{source_name}_seed1_pp2.jsonl",
        }
        outputs = {
            "pp1": WORKLOADS / f"hongo_peak_{level}x_repeat600_seed1.jsonl",
            "pp2": WORKLOADS / f"hongo_peak_{level}x_repeat600_seed1_pp2.jsonl",
        }
        pp1 = repeat600.load(sources["pp1"])
        pp2 = repeat600.load(sources["pp2"])
        pp1_identity = [(row["request_id"], row.get("session_id")) for row in pp1]
        pp2_identity = [(row["request_id"], row.get("session_id")) for row in pp2]
        if pp1_identity != pp2_identity:
            raise ValueError(f"Peak {level}x PP1 and PP2 request order differs")
        repeat600.write(outputs["pp1"], repeat600.repeat(pp1))
        repeat600.write(outputs["pp2"], repeat600.repeat(pp2))


if __name__ == "__main__":
    main()
