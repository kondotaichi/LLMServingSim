#!/usr/bin/env python3
"""Run the Hongo builder with outputs isolated to this experiment.

The original builder is reused as the single source of geographic and network
semantics, but its output globals are redirected so prior experiment assets are
never overwritten.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
EXPERIMENT = HERE.parents[1]
SOURCE = (
    REPO
    / "experiments/2026-08-01_hongo_workload/scripts/prepare_hongo_workload.py"
)


def main() -> None:
    spec = importlib.util.spec_from_file_location("hongo_workload_builder", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXPERIMENT_DIR = EXPERIMENT
    module.CONFIG_DIR = EXPERIMENT / "configs"
    module.PLACEMENT_DIR = EXPERIMENT / "placements"
    module.WORKLOAD_DIR = EXPERIMENT / "workloads" / "full"
    module.main()


if __name__ == "__main__":
    main()
