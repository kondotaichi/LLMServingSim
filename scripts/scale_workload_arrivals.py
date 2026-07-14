#!/usr/bin/env python3
"""Scale a JSONL workload's arrival span while preserving request contents."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float, required=True)
    args = parser.parse_args()

    if args.scale <= 0:
        raise ValueError("--scale must be positive")

    with args.input.open() as file:
        rows = [json.loads(line) for line in file if line.strip()]
    if not rows:
        raise ValueError("input workload is empty")

    base_arrival = min(int(row["arrival_time_ns"]) for row in rows)
    for row in rows:
        old_arrival = int(row["arrival_time_ns"])
        new_arrival = base_arrival + round((old_arrival - base_arrival) * args.scale)
        arrival_shift = new_arrival - old_arrival
        row["arrival_time_ns"] = new_arrival

        # Preserve the original request-send to GPU-arrival communication
        # interval while moving the request along the scaled arrival timeline.
        if "request_send_time_ns" in row:
            row["request_send_time_ns"] = int(row["request_send_time_ns"]) + arrival_shift

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as file:
        for row in rows:
            file.write(json.dumps(row, separators=(",", ":")) + "\n")

    arrivals = [int(row["arrival_time_ns"]) for row in rows]
    print(f"requests: {len(rows)}")
    print(f"first arrival: {min(arrivals)} ns")
    print(f"last arrival: {max(arrivals)} ns")
    print(f"arrival span: {(max(arrivals) - min(arrivals)) / 1e9:.6f} s")
    print(args.output)


if __name__ == "__main__":
    main()
