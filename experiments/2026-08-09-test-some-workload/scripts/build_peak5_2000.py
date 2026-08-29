#!/usr/bin/env python3
"""Build 2,000-request Peak 5x PP=1 and PP=2 workloads from Peak 2x."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ROOT / "workloads/full"
FACTOR = 2.0 / 5.0


def build(source: Path, destination: Path) -> None:
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    if len(rows) != 2000:
        raise ValueError(f"{source} has {len(rows)} requests, expected 2000")
    with destination.open("w", encoding="utf-8") as handle:
        for row in rows:
            new_row = dict(row)
            send_ns = round(int(row["request_send_time_ns"]) * FACTOR)
            uplink_ns = int(row["arrival_time_ns"]) - int(row["request_send_time_ns"])
            new_row["request_send_time_ns"] = send_ns
            new_row["arrival_time_ns"] = send_ns + uplink_ns
            new_row["workload_level"] = "peak_5x"
            handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")
    print(f"wrote {destination} ({len(rows)} requests)")


def main() -> None:
    build(
        WORKLOADS / "hongo_peak_2x_seed1.jsonl",
        WORKLOADS / "hongo_peak_5x_seed1.jsonl",
    )
    build(
        WORKLOADS / "hongo_peak_2x_seed1_pp2.jsonl",
        WORKLOADS / "hongo_peak_5x_seed1_pp2.jsonl",
    )


if __name__ == "__main__":
    main()
