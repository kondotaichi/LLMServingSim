#!/usr/bin/env python3
"""Compare Azure replay metrics with the August 1 local 12-GPU simulation."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXP_ROOT.parents[1]
LOCAL_ROOT = REPO_ROOT / "experiments/2026-08-01_hongo_workload/results"
OUTPUT = EXP_ROOT / "analysis/azure_vs_local_proposed_pp2.csv"


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def metrics(ttft_ns: list[float], tpot_ns: list[float]) -> dict[str, float]:
    result = {}
    for name, values in (("ttft", ttft_ns), ("tpot", tpot_ns)):
        milliseconds = [value / 1e6 for value in values]
        result[f"{name}_mean_ms"] = sum(milliseconds) / len(milliseconds)
        for percent in (50, 95, 99):
            result[f"{name}_p{percent}_ms"] = percentile(milliseconds, percent)
    return result


def remote_results(level: int) -> tuple[list[dict], int]:
    path = (
        EXP_ROOT
        / f"results/remote/peak_{level}x_repeat600_v3_20260819"
        / f"azure-peak-{level}x-repeat600-v3-20260819/requests.jsonl"
    )
    latest = {}
    attempts = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            attempts += 1
            request_id = int(row["request_id"])
            if request_id not in latest or int(row["attempt"]) > int(latest[request_id]["attempt"]):
                latest[request_id] = row
    return [latest[index] for index in sorted(latest)], attempts


def local_results(level: int) -> list[dict]:
    if level == 1:
        run = "busy_hour_seed1_4_redirect_kv_pp2"
    else:
        run = f"peak_{level}x_seed1_4_redirect_kv_pp2"
    with (LOCAL_ROOT / run / "requests.csv").open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    rows = []
    for level in range(1, 11):
        remote, attempts = remote_results(level)
        remote_ok = [row for row in remote if row["success"]]
        local = local_results(level)
        local_metrics = metrics(
            [float(row["e2e_ttft_ns"]) for row in local],
            [float(row["TPOT"]) for row in local],
        )
        for window, selected in (
            ("full", remote_ok),
            ("first_300", [row for row in remote_ok if int(row["request_id"]) < 300]),
            ("second_300", [row for row in remote_ok if int(row["request_id"]) >= 300]),
        ):
            remote_metrics = metrics(
                [float(row["e2e_ttft_ns"]) for row in selected],
                [float(row["tpot_ns"]) for row in selected],
            )
            output = {
                "level": level,
                "target_rps": level * 9,
                "remote_window": window,
                "remote_requests": len(selected),
                "remote_attempts_total": attempts,
                "remote_failures_after_retry": len(remote) - len(remote_ok),
                "local_requests": len(local),
                "local_arm": "4_redirect_kv_pp2",
            }
            output.update({f"remote_{key}": value for key, value in remote_metrics.items()})
            output.update({f"local_{key}": value for key, value in local_metrics.items()})
            for metric in remote_metrics:
                output[f"delta_{metric}_pct"] = (
                    remote_metrics[metric] / local_metrics[metric] - 1
                ) * 100
            rows.append(output)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
