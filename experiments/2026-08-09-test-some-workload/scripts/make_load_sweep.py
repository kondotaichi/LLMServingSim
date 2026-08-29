#!/usr/bin/env python3
"""Create load variants by compressing a workload timeline only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--base-multiplier", required=True, type=float)
    parser.add_argument("--multipliers", required=True, nargs="+", type=float)
    args = parser.parse_args()
    if args.base_multiplier <= 0 or any(value <= 0 for value in args.multipliers):
        raise ValueError("multipliers must be positive")

    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for multiplier in args.multipliers:
        factor = args.base_multiplier / multiplier
        output = args.output_dir / f"load_{multiplier:g}x.jsonl"
        with output.open("w") as handle:
            for row in rows:
                new_row = dict(row)
                send = round(int(row.get("request_send_time_ns", row["arrival_time_ns"])) * factor)
                if "request_send_time_ns" in row:
                    uplink = int(row["arrival_time_ns"]) - int(row["request_send_time_ns"])
                    new_row["request_send_time_ns"] = send
                    new_row["arrival_time_ns"] = send + uplink
                else:
                    new_row["arrival_time_ns"] = send
                new_row["workload_level"] = f"load_{multiplier:g}x"
                handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")
        print(output)


if __name__ == "__main__":
    main()

