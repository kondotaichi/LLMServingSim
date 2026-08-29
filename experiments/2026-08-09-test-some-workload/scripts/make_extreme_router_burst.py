#!/usr/bin/env python3
"""Create a small all-at-once burst for exercising router capacity waits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--num-requests", type=int, default=120)
    parser.add_argument("--home-instance", type=int, default=0)
    parser.add_argument("--balanced-warmup", type=int, default=0)
    parser.add_argument("--num-instances", type=int, default=1)
    parser.add_argument("--overflow-delay-ns", type=int, default=0)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    rows = rows[: args.num_requests]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as handle:
        for position, row in enumerate(rows):
            new_row = dict(row)
            uplink_ns = int(row.get("uplink_latency_ns", 0))
            is_warmup = position < args.balanced_warmup
            send_time_ns = 0 if is_warmup else args.overflow_delay_ns
            home_instance = (
                position % args.num_instances
                if is_warmup else args.home_instance
            )
            new_row["request_send_time_ns"] = send_time_ns
            new_row["arrival_time_ns"] = send_time_ns + uplink_ns
            new_row["assigned_instance_id"] = home_instance
            new_row["gpu_id"] = home_instance
            new_row["nearest_gpu_id"] = home_instance
            new_row["extreme_router_burst"] = True
            handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")
    print(
        f"{args.output}: {len(rows)} requests, "
        f"warmup={args.balanced_warmup}, overflow_delay_ns={args.overflow_delay_ns}, "
        f"home={args.home_instance}"
    )


if __name__ == "__main__":
    main()
