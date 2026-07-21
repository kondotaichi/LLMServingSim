#!/usr/bin/env python3
"""Generate mixed input/reuse workloads with reproducible burst phases."""

import csv
import json
import math
import random
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
SOURCE = (
    REPO_ROOT
    / "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl"
)
OUTPUT_DIR = EXPERIMENT_DIR / "workloads"
MANIFEST = EXPERIMENT_DIR / "configs/workload_manifest.csv"
INPUT_SIZES = (512, 2000, 4000, 6000, 8000, 10000)
REUSE_RATIOS = (0.0, 0.25, 0.5)
REQUEST_RATES = (2.5, 3.33)
SEEDS = (1, 2, 3)
BLOCK_SIZE = 16
BURST_WINDOWS = ((75, 105), (195, 225))
BURST_INTERVAL_FRACTION = sum(end - start for start, end in BURST_WINDOWS) / 300
BURST_RATE_MULTIPLIER = 3.0
NORMAL_RATE_MULTIPLIER = (
    (1.0 - BURST_INTERVAL_FRACTION)
    + BURST_INTERVAL_FRACTION / BURST_RATE_MULTIPLIER
)


def rate_label(rate):
    return str(rate).replace(".", "p")


def is_burst(request_index):
    return any(start <= request_index < end for start, end in BURST_WINDOWS)


def mixed_assignments(rng, count):
    assignments = [None] * count
    for burst_phase in (False, True):
        positions = [
            index for index in range(count)
            if is_burst(index) == burst_phase
        ]
        input_values = [
            value for value in INPUT_SIZES
            for _ in range(len(positions) // len(INPUT_SIZES))
        ]
        reuse_values = [
            value for value in REUSE_RATIOS
            for _ in range(len(positions) // len(REUSE_RATIOS))
        ]
        if len(input_values) != len(positions) or len(reuse_values) != len(positions):
            raise ValueError(
                "Phase request counts must divide evenly across mixed values"
            )
        rng.shuffle(input_values)
        rng.shuffle(reuse_values)
        for position, assignment in zip(
            positions, zip(input_values, reuse_values)
        ):
            assignments[position] = assignment
    return assignments


def arrivals(rng, count, target_rate):
    normal_rate = target_rate * NORMAL_RATE_MULTIPLIER
    burst_rate = normal_rate * BURST_RATE_MULTIPLIER
    intervals = [
        rng.expovariate(burst_rate if is_burst(index) else normal_rate)
        for index in range(count)
    ]
    target_duration = count / target_rate
    scale = target_duration / sum(intervals)
    arrival = 0.0
    values = []
    for interval in intervals:
        arrival += interval * scale
        values.append(round(arrival * 1e9))
    return values, normal_rate, burst_rate


def prepare(source_rows, target_rate, seed):
    rng = random.Random(seed * 1_000_003 + round(target_rate * 1000))
    source_order = list(range(len(source_rows)))
    rng.shuffle(source_order)
    assignments = mixed_assignments(rng, len(source_rows))
    arrival_times, normal_rate, burst_rate = arrivals(
        rng, len(source_rows), target_rate
    )
    prepared = []
    for request_id, (source_index, assignment, arrival_ns) in enumerate(
        zip(source_order, assignments, arrival_times)
    ):
        input_tokens, reuse_ratio = assignment
        row = dict(source_rows[source_index])
        input_ids = row["input_tok_ids"]
        if len(input_ids) < input_tokens:
            raise ValueError(
                f"Source request {source_index} has {len(input_ids)} input tokens; "
                f"required {input_tokens}"
            )
        reuse_tokens = math.floor(input_tokens * reuse_ratio)
        reuse_tokens = reuse_tokens // BLOCK_SIZE * BLOCK_SIZE
        row.update({
            "request_id": request_id,
            "input_toks": input_tokens,
            "input_tok_ids": input_ids[:input_tokens],
            "reuse_prefix_toks": reuse_tokens,
            "request_payload_bytes": 500 + input_tokens * 4,
            "arrival_time_ns": arrival_ns,
            "request_send_time_ns": arrival_ns,
            "mixed_reuse_ratio": reuse_ratio,
            "mixed_traffic_phase": "burst" if is_burst(request_id) else "normal",
        })
        prepared.append(row)
    return prepared, normal_rate, burst_rate


def validate(rows, target_rate):
    if len(rows) != 300:
        raise ValueError(f"Expected 300 requests, found {len(rows)}")
    if any(
        left["arrival_time_ns"] >= right["arrival_time_ns"]
        for left, right in zip(rows, rows[1:])
    ):
        raise ValueError("Arrival timestamps must be strictly increasing")
    input_counts = Counter(row["input_toks"] for row in rows)
    reuse_counts = Counter(row["mixed_reuse_ratio"] for row in rows)
    if input_counts != Counter({value: 50 for value in INPUT_SIZES}):
        raise ValueError(f"Unexpected input distribution: {input_counts}")
    if reuse_counts != Counter({value: 100 for value in REUSE_RATIOS}):
        raise ValueError(f"Unexpected reuse distribution: {reuse_counts}")
    realized_rate = len(rows) / (rows[-1]["arrival_time_ns"] / 1e9)
    if not math.isclose(realized_rate, target_rate, rel_tol=1e-6):
        raise ValueError(
            f"Realized rate {realized_rate} differs from target {target_rate}"
        )
    for row in rows:
        if row["reuse_prefix_toks"] % BLOCK_SIZE:
            raise ValueError("Reuse tokens must be block aligned")
        if row["reuse_prefix_toks"] > row["input_toks"]:
            raise ValueError("Reuse tokens cannot exceed input tokens")


def realized_phase_rates(rows):
    durations = {"normal": 0.0, "burst": 0.0}
    counts = Counter()
    previous_arrival_ns = 0
    for row in rows:
        phase = row["mixed_traffic_phase"]
        durations[phase] += (
            row["arrival_time_ns"] - previous_arrival_ns
        ) / 1e9
        counts[phase] += 1
        previous_arrival_ns = row["arrival_time_ns"]
    return {
        phase: counts[phase] / durations[phase]
        for phase in durations
    }


def main():
    with SOURCE.open(encoding="utf-8") as source:
        source_rows = [json.loads(line) for line in source if line.strip()]
    if len(source_rows) != 300:
        raise ValueError(f"Expected 300 source requests, found {len(source_rows)}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    for target_rate in REQUEST_RATES:
        for seed in SEEDS:
            condition = f"mixed_rate{rate_label(target_rate)}_seed{seed}"
            rows, normal_rate, burst_rate = prepare(
                source_rows, target_rate, seed
            )
            validate(rows, target_rate)
            output_path = OUTPUT_DIR / f"{condition}.jsonl"
            with output_path.open("w", encoding="utf-8") as output:
                for row in rows:
                    output.write(json.dumps(row, separators=(",", ":")) + "\n")
            phase_rates = realized_phase_rates(rows)
            manifest.append({
                "condition": condition,
                "target_rate_rps": target_rate,
                "seed": seed,
                "requests": len(rows),
                "duration_s": rows[-1]["arrival_time_ns"] / 1e9,
                "realized_rate_rps": len(rows) / (rows[-1]["arrival_time_ns"] / 1e9),
                "normal_nominal_rate_rps": normal_rate,
                "burst_nominal_rate_rps": burst_rate,
                "normal_realized_rate_rps": phase_rates["normal"],
                "burst_realized_rate_rps": phase_rates["burst"],
                "normal_requests": sum(row["mixed_traffic_phase"] == "normal" for row in rows),
                "burst_requests": sum(row["mixed_traffic_phase"] == "burst" for row in rows),
                "workload": str(output_path.relative_to(REPO_ROOT)),
            })
    with MANIFEST.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    print(f"Prepared {len(manifest)} workloads in {OUTPUT_DIR}")
    print(f"Manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
