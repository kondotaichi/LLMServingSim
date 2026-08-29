#!/usr/bin/env python3
"""Expand PP2 logical homes into paired PP1 physical-GPU homes."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    per_group_count = Counter()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as handle:
        for row in rows:
            group = int(row["assigned_instance_id"])
            physical = group * 2 + per_group_count[group] % 2
            per_group_count[group] += 1
            new_row = dict(row)
            new_row["pp2_source_group_id"] = group
            new_row["assigned_instance_id"] = physical
            new_row["gpu_id"] = physical
            new_row["nearest_gpu_id"] = physical
            handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()

