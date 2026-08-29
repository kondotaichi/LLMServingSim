#!/usr/bin/env python3
"""Map the Hongo 1x-10x workload sweep onto both geographic layouts."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
HONGO_DIR = EXPERIMENT_DIR.parent / "2026-08-01_hongo_workload"
REFERENCE_SCRIPT = EXPERIMENT_DIR / "scripts" / "prepare_reference_workloads.py"


def load_reference_module():
    spec = importlib.util.spec_from_file_location("prepare_reference_workloads", REFERENCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {REFERENCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-reqs", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--pp-size", type=int, choices=(1, 2), default=2)
    return parser.parse_args()


def load_name(multiplier: int) -> str:
    return "busy_hour" if multiplier == 1 else f"peak_{multiplier}x"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    args = parse_args()
    reference = load_reference_module()
    for multiplier in range(1, 11):
        name = load_name(multiplier)
        source = HONGO_DIR / "workloads" / f"hongo_{name}_seed{args.seed}.jsonl"
        if not source.is_file():
            raise FileNotFoundError(
                f"Missing Hongo workload: {source}. Generate the Hongo 1x-10x sweep first."
            )
        source_rows = reference.read_jsonl(source, args.num_reqs)
        if len(source_rows) != args.num_reqs:
            raise ValueError(f"{source} contains {len(source_rows)} rows, expected {args.num_reqs}")
        for row in source_rows:
            row["hongo_request_id"] = int(row["request_id"])

        for layout in ("all_tokyo", "kagoshima_tokyo"):
            rows = reference.transform(source_rows, layout, rate=None, pp_size=args.pp_size)
            for row in rows:
                row["workload_level"] = f"hongo_{name}"
                row["hongo_load_multiplier"] = multiplier
            output = (
                EXPERIMENT_DIR
                / "workloads"
                / f"{layout}_pp{args.pp_size}"
                / f"hongo_{name}_seed{args.seed}.jsonl"
            )
            write_jsonl(output, rows)
            print(f"wrote {output} ({len(rows)} requests, {multiplier}x)")


if __name__ == "__main__":
    main()
