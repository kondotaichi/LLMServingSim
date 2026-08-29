#!/usr/bin/env python3
"""Create a deterministic PP2 hotspot while preserving content and timing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--hot-instance", type=int, default=0)
    parser.add_argument("--hot-share", type=float, default=0.5)
    parser.add_argument("--num-instances", type=int, default=6)
    args = parser.parse_args()
    if not 0.0 < args.hot_share < 1.0:
        raise ValueError("--hot-share must be between 0 and 1")
    if not 0 <= args.hot_instance < args.num_instances:
        raise ValueError("--hot-instance is out of range")

    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    cold_instances = [i for i in range(args.num_instances) if i != args.hot_instance]
    hot_count = round(len(rows) * args.hot_share)
    # Spread hotspot requests across the full timeline rather than creating a
    # front-loaded burst. The accumulator implements deterministic weighted
    # round-robin and is independent of outcomes from any routing method.
    accumulator = 0.0
    assigned_hot = 0
    cold_index = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as handle:
        for index, row in enumerate(rows):
            remaining = len(rows) - index
            need_hot = hot_count - assigned_hot
            accumulator += need_hot / remaining if remaining else 0.0
            if accumulator >= 1.0:
                instance = args.hot_instance
                accumulator -= 1.0
                assigned_hot += 1
            else:
                instance = cold_instances[cold_index % len(cold_instances)]
                cold_index += 1
            new_row = dict(row)
            new_row["assigned_instance_id"] = instance
            new_row["gpu_id"] = instance
            new_row["nearest_gpu_id"] = instance
            new_row["spatial_skew_hot_instance"] = args.hot_instance
            new_row["spatial_skew_hot_share"] = args.hot_share
            handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")
    print(f"{args.output}: hotspot={assigned_hot}/{len(rows)} ({assigned_hot / len(rows):.1%})")


if __name__ == "__main__":
    main()
