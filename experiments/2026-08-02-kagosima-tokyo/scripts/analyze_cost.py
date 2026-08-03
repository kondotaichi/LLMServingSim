#!/usr/bin/env python3
"""
Post-simulation cost analysis for the Kagoshima-Tokyo experiment.

For each completed arm, computes:
  - Mean / p50 / p95 / p99 TTFT (ms)
  - TTFT split by region (tokyo-assigned vs kagoshima-assigned requests)
  - GPU utilization from timeseries
  - Estimated electricity cost (yen) using RTX4090 TDP and regional rates

Electricity rates
-----------------
  Tokyo    : 23.0 yen / kWh
  Kagoshima: 14.7 yen / kWh

RTX4090 TDP: 450 W (used as active power; idle assumed 50 W)

Usage
-----
  python3 experiments/2026-08-02-kagosima-tokyo/scripts/analyze_cost.py
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR    = EXPERIMENT_DIR / "results"
ANALYSIS_DIR   = EXPERIMENT_DIR / "analysis"

TOKYO_RATE_YEN_PER_KWH     = 23.0
KAGOSHIMA_RATE_YEN_PER_KWH = 14.7
RTX4090_TDP_W              = 450.0
RTX4090_IDLE_W             = 50.0
NUM_TOKYO_GPUS_KG          = 6
NUM_KAGOSHIMA_GPUS_KG      = 6
NUM_TOKYO_GPUS_AT          = 12  # all-tokyo baseline

ARMS = {
    "all_tokyo":  {"region_split": False, "label": "All-Tokyo PP=2+spec"},
    "kg_pp2":     {"region_split": True,  "label": "6+6 PP=2"},
    "kg_pp2_spec":{"region_split": True,  "label": "6+6 PP=2+spec"},
}
LOADS = ["heavy"]


def read_requests(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def read_gpu_utilization(path: Path) -> tuple[float, float]:
    """Return (mean_utilization_0_to_1, simulation_duration_s)."""
    if not path.exists():
        return 0.0, 0.0
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0.0, 0.0
    # column is utilization_pct (0-100); time column is time_since_start_s
    utils = [float(r["utilization_pct"]) / 100.0 for r in rows if r.get("utilization_pct")]
    duration_s = max(float(r.get("time_since_start_s", 0)) for r in rows)
    # add one window width (1 s) to cover the last window
    duration_s += float(rows[0].get("window_duration_ns", 1_000_000_000)) / 1e9
    return (statistics.mean(utils) if utils else 0.0), duration_s


def ttft_stats(values_ms: list[float]) -> dict:
    if not values_ms:
        return {"mean": 0, "p50": 0, "p95": 0, "p99": 0, "n": 0}
    s = sorted(values_ms)
    n = len(s)
    return {
        "mean": statistics.mean(s),
        "p50":  s[int(n * 0.50)],
        "p95":  s[int(n * 0.95)],
        "p99":  s[int(n * 0.99)],
        "n":    n,
    }


def electricity_cost_yen(
    mean_util: float,
    duration_s: float,
    n_tokyo: int,
    n_kagoshima: int,
) -> dict:
    """Estimate electricity cost for the simulation window."""
    active_w  = RTX4090_IDLE_W + mean_util * (RTX4090_TDP_W - RTX4090_IDLE_W)
    energy_j_tokyo     = active_w * duration_s * n_tokyo
    energy_j_kagoshima = active_w * duration_s * n_kagoshima
    cost_tokyo     = energy_j_tokyo     / 3_600_000 * TOKYO_RATE_YEN_PER_KWH
    cost_kagoshima = energy_j_kagoshima / 3_600_000 * KAGOSHIMA_RATE_YEN_PER_KWH
    return {
        "mean_gpu_util": mean_util,
        "duration_s":    duration_s,
        "cost_tokyo_yen":     cost_tokyo,
        "cost_kagoshima_yen": cost_kagoshima,
        "cost_total_yen":     cost_tokyo + cost_kagoshima,
        "n_tokyo_gpus":     n_tokyo,
        "n_kagoshima_gpus": n_kagoshima,
    }


def analyze_arm(arm_name: str, load: str, region_split: bool) -> dict | None:
    req_path = RESULTS_DIR / arm_name / load / "requests.csv"
    util_path = RESULTS_DIR / arm_name / load / "gpu_utilization_timeseries.csv"
    if not req_path.exists():
        return None

    rows = read_requests(req_path)
    all_ttft = []
    tokyo_ttft = []
    kg_ttft = []
    for r in rows:
        # e2e_ttft_ns is the end-to-end TTFT including network latency, in ns
        ttft_ms = float(r.get("e2e_ttft_ns") or r.get("TTFT") or 0) / 1e6
        all_ttft.append(ttft_ms)
        if region_split:
            # Kagoshima GPUs have gpu_x_m > 500_000 (virtual offset 1,050,000 m)
            gpu_x = float(r.get("gpu_x_m") or 0)
            if gpu_x > 500_000:
                kg_ttft.append(ttft_ms)
            else:
                tokyo_ttft.append(ttft_ms)

    mean_util, duration_s = read_gpu_utilization(util_path)

    if arm_name.startswith("all_tokyo"):
        n_tokyo, n_kg = NUM_TOKYO_GPUS_AT, 0
    else:
        n_tokyo, n_kg = NUM_TOKYO_GPUS_KG, NUM_KAGOSHIMA_GPUS_KG

    cost = electricity_cost_yen(mean_util, duration_s, n_tokyo, n_kg)

    result = {
        "arm":  arm_name,
        "load": load,
        **{f"all_{k}": v for k, v in ttft_stats(all_ttft).items()},
        "cost_total_yen":        cost["cost_total_yen"],
        "cost_tokyo_yen":        cost["cost_tokyo_yen"],
        "cost_kagoshima_yen":    cost["cost_kagoshima_yen"],
        "mean_gpu_util":         cost["mean_gpu_util"],
        "duration_s":            cost["duration_s"],
        "n_tokyo_gpus":          n_tokyo,
        "n_kagoshima_gpus":      n_kg,
    }
    if region_split:
        result.update({f"tokyo_{k}": v for k, v in ttft_stats(tokyo_ttft).items()})
        result.update({f"kg_{k}": v for k, v in ttft_stats(kg_ttft).items()})

    return result


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for arm_name, cfg in ARMS.items():
        for load in LOADS:
            result = analyze_arm(arm_name, load, cfg["region_split"])
            if result is None:
                print(f"  [skip] {arm_name}/{load} (no results yet)")
                continue
            result["label"] = cfg["label"]
            rows.append(result)
            print(f"  [{arm_name}/{load}] "
                  f"TTFT mean={result['all_mean']:.1f}ms "
                  f"cost={result['cost_total_yen']:.4f}yen "
                  f"util={result['mean_gpu_util']:.2f}")

    if not rows:
        print("No results found. Run run_experiments.sh first.")
        return

    out_path = ANALYSIS_DIR / "summary.csv"
    # collect union of all keys so rows with region_split fields don't break the writer
    fieldnames = list(dict.fromkeys(k for r in rows for k in r.keys()))
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"\nSummary written to {out_path}")

    # Cost comparison table vs All-Tokyo baseline per load level
    print("\n── Cost savings vs All-Tokyo (18% theoretical) ──")
    at_cost = {r["load"]: r["cost_total_yen"]
               for r in rows if r["arm"] == "all_tokyo_pp2_spec"}
    for r in rows:
        if r["arm"] == "all_tokyo_pp2_spec":
            continue
        baseline = at_cost.get(r["load"])
        if baseline and baseline > 0:
            saving_pct = (baseline - r["cost_total_yen"]) / baseline * 100
            print(f"  {r['arm']:<30s} {r['load']:<15s} "
                  f"saving={saving_pct:+.1f}%  "
                  f"TTFT_mean={r['all_mean']:.1f}ms")


if __name__ == "__main__":
    main()
