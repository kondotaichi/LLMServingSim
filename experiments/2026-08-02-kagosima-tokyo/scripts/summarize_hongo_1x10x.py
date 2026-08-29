#!/usr/bin/env python3
"""Create one summary table for the geographic Hongo 1x-10x sweep."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path


ARMS = ("all_tokyo_baseline", "kg_baseline", "kg_proposed")


def truthy(value: str | None) -> bool:
    return str(value).lower() in ("1", "true")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()

    summary = []
    for multiplier in range(1, 11):
        for arm in ARMS:
            path = args.results / f"{multiplier}x" / arm / "requests.csv"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            ttft = [float(row["e2e_ttft_ns"]) / 1e6 for row in rows]
            summary.append({
                "load_multiplier": multiplier,
                "arm": arm,
                "requests": len(rows),
                "mean_ttft_ms": statistics.mean(ttft),
                "p50_ttft_ms": percentile(ttft, 0.50),
                "p95_ttft_ms": percentile(ttft, 0.95),
                "p99_ttft_ms": percentile(ttft, 0.99),
                "redirects": sum(truthy(row.get("rerouted")) for row in rows),
                "prewarm_hits": sum(truthy(row.get("proactive_kv_prewarm_hit")) for row in rows),
                "prewarm_wasted": sum(truthy(row.get("proactive_kv_prewarm_wasted")) for row in rows),
            })

    output = args.results / "summary.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not summary:
        raise FileNotFoundError(f"No completed request CSVs found below {args.results}")
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary)
    print(f"wrote {output} ({len(summary)} completed arms)")


if __name__ == "__main__":
    main()
