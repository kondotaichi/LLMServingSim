#!/usr/bin/env python3
"""Summarize a 2x2 PP by KV-migration experiment."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ARMS = ("pp1_cold", "pp1_kv", "pp2_cold", "pp2_kv")


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def summarize(path: Path) -> dict:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    ttft = [float(row["e2e_ttft_ns"]) / 1e6 for row in rows]
    redirects = sum(int(row.get("rerouted", 0) or 0) for row in rows)
    kv_latency = [float(row.get("kv_migration_latency_ns", 0) or 0) / 1e6 for row in rows]
    return {
        "requests": len(rows),
        "redirects": redirects,
        "redirect_rate": redirects / len(rows) if rows else 0.0,
        "mean_ttft_ms": sum(ttft) / len(ttft),
        "p50_ttft_ms": percentile(ttft, 0.50),
        "p95_ttft_ms": percentile(ttft, 0.95),
        "p99_ttft_ms": percentile(ttft, 0.99),
        "mean_kv_migration_ms": sum(kv_latency) / len(kv_latency),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results_dir", type=Path)
    args = parser.parse_args()
    results = {
        arm: summarize(args.results_dir / arm / "requests.csv") for arm in ARMS
    }
    interactions = {}
    for metric in ("mean_ttft_ms", "p50_ttft_ms", "p95_ttft_ms", "p99_ttft_ms"):
        pp1_gain = results["pp1_cold"][metric] - results["pp1_kv"][metric]
        pp2_gain = results["pp2_cold"][metric] - results["pp2_kv"][metric]
        interactions[metric] = {
            "pp1_kv_gain_ms": pp1_gain,
            "pp2_kv_gain_ms": pp2_gain,
            "pp_x_kv_interaction_ms": pp2_gain - pp1_gain,
        }
    print(json.dumps({"arms": results, "interactions": interactions}, indent=2))


if __name__ == "__main__":
    main()
