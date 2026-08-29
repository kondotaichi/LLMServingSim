#!/usr/bin/env python3
import csv
import glob
import json
import math
import os
import re
import statistics


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
LOCAL_ROOT = os.path.join(
    REPO_ROOT,
    "experiments/2026-08-21-local-hongo-workload-gh200-test/results_miyabi_matched",
)
MIYABI_ROOT = os.path.join(
    REPO_ROOT,
    "experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/results",
)
OUTPUT_ROOT = os.path.join(
    REPO_ROOT,
    "experiments/2026-08-21-local-hongo-workload-gh200-test/analysis_miyabi_matched",
)


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values):
    return {
        "mean": statistics.mean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def summarize_local(path):
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    ttft = [float(row["e2e_ttft_ns"]) / 1e6 for row in rows]
    completion = [float(row["request_completion_latency_ns"]) / 1e6 for row in rows]
    tpot_rows = [row for row in rows if int(row["output"]) > 1]
    tpot = [float(row["TPOT"]) / 1e6 for row in tpot_rows]
    token_count = sum(int(row["output"]) - 1 for row in tpot_rows)
    weighted_tpot = sum(
        float(row["TPOT"]) * (int(row["output"]) - 1) for row in tpot_rows
    ) / token_count / 1e6
    return summarize(ttft), summarize(tpot), summarize(completion), weighted_tpot, len(rows)


def select_miyabi_run(level):
    candidates = []
    pattern = os.path.join(
        MIYABI_ROOT, f"miyabi-peak-{level}x-repeat600-full-*/summary.json"
    )
    for path in glob.glob(pattern):
        with open(path) as handle:
            summary = json.load(handle)
        if summary.get("accepted") and summary.get("successful_requests") == 600:
            candidates.append(path)
    return sorted(candidates)[-1]


def summarize_miyabi(summary_path):
    requests_path = summary_path.replace("summary.json", "requests.jsonl")
    with open(requests_path) as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    rows = [row for row in rows if row.get("success")]
    ttft = [row["e2e_ttft_ns"] / 1e6 for row in rows]
    completion = [row["completion_latency_ns"] / 1e6 for row in rows]
    tpot_rows = [row for row in rows if row.get("actual_output_tokens", 0) > 1]
    tpot = [row["tpot_ns"] / 1e6 for row in tpot_rows]
    token_count = sum(row["actual_output_tokens"] - 1 for row in tpot_rows)
    weighted_tpot = sum(
        row["tpot_ns"] * (row["actual_output_tokens"] - 1) for row in tpot_rows
    ) / token_count / 1e6
    return summarize(ttft), summarize(tpot), summarize(completion), weighted_tpot, len(rows)


def main():
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    records = []
    methods = (("nearest_kv", "1_nearest_kv"), ("load", "2_load"))
    for level in range(1, 11):
        miyabi_path = select_miyabi_run(level)
        (
            miyabi_ttft,
            miyabi_tpot,
            miyabi_completion,
            miyabi_weighted,
            miyabi_count,
        ) = summarize_miyabi(miyabi_path)
        for method, suffix in methods:
            pattern = os.path.join(
                LOCAL_ROOT, f"peak_{level}x_repeat600_seed1_miyabi_matched_{suffix}",
                "requests.csv",
            )
            if not os.path.isfile(pattern):
                continue
            (
                local_ttft,
                local_tpot,
                local_completion,
                local_weighted,
                local_count,
            ) = summarize_local(pattern)
            record = {
                "peak": level,
                "local_method": method,
                "local_requests": local_count,
                "miyabi_requests": miyabi_count,
                "miyabi_run": os.path.basename(os.path.dirname(miyabi_path)),
            }
            for metric, local_values, miyabi_values in (
                ("ttft", local_ttft, miyabi_ttft),
                ("tpot", local_tpot, miyabi_tpot),
                ("completion", local_completion, miyabi_completion),
            ):
                for stat in ("mean", "p50", "p95", "p99"):
                    local_value = local_values[stat]
                    miyabi_value = miyabi_values[stat]
                    record[f"local_{metric}_{stat}_ms"] = local_value
                    record[f"miyabi_{metric}_{stat}_ms"] = miyabi_value
                    record[f"miyabi_over_local_{metric}_{stat}"] = (
                        miyabi_value / local_value
                    )
            record["local_weighted_tpot_ms"] = local_weighted
            record["miyabi_weighted_tpot_ms"] = miyabi_weighted
            record["miyabi_over_local_weighted_tpot"] = miyabi_weighted / local_weighted
            records.append(record)

    output_path = os.path.join(OUTPUT_ROOT, "local_vs_miyabi_summary.csv")
    with open(output_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(output_path)


if __name__ == "__main__":
    main()
