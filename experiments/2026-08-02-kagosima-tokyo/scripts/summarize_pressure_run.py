#!/usr/bin/env python3
"""Print the routing and prewarm signals needed to select a pressure level."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path


def truthy(value: str | None) -> bool:
    return str(value).lower() in ("1", "true")


def number(row: dict, key: str) -> float:
    return float(row.get(key) or 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("requests_csv", type=Path)
    args = parser.parse_args()
    with args.requests_csv.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    redirects = sum(truthy(row.get("rerouted")) for row in rows)
    hits = sum(truthy(row.get("proactive_kv_prewarm_hit")) for row in rows)
    wasted = sum(truthy(row.get("proactive_kv_prewarm_wasted")) for row in rows)
    ttft_ms = [number(row, "e2e_ttft_ns") / 1e6 for row in rows]
    queue_ms = [number(row, "queueing_before_ttft_ns") / 1e6 for row in rows]
    print(f"requests={len(rows)}")
    print(f"redirects={redirects} ({redirects / len(rows) * 100:.1f}%)")
    print(f"prewarm_hits={hits}")
    print(f"prewarm_wasted={wasted}")
    print(f"mean_ttft_ms={statistics.mean(ttft_ms):.1f}")
    print(f"mean_queueing_ms={statistics.mean(queue_ms):.1f}")


if __name__ == "__main__":
    main()
