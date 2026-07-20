#!/usr/bin/env python3
"""Prepare the Phase 1 capacity-boundary workload matrix."""

import json
import math
import random
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
SOURCE = (
    REPO_ROOT
    / "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl"
)
OUTPUT_DIR = EXPERIMENT_DIR / "workloads/phase1"
INPUT_SIZES = (4000, 6000, 8000)
REQUEST_RATES = (2.5, 3.33, 5.0)
REUSE_RATIOS = (0.0, 0.5)
SEEDS = (1, 2, 3)
BLOCK_SIZE = 16


def rate_label(rate):
    return str(rate).replace(".", "p")


def condition_name(input_tokens, rate, reuse_ratio, seed):
    reuse_percent = round(reuse_ratio * 100)
    return (
        f"input{input_tokens}_rate{rate_label(rate)}_"
        f"reuse{reuse_percent:02d}_seed{seed}"
    )


def prepare(rows, input_tokens, rate, reuse_ratio, seed):
    rng = random.Random(seed * 1_000_003 + input_tokens * 101 + round(rate * 100))
    order = list(range(len(rows)))
    rng.shuffle(order)
    arrival_ns = 0
    reuse_tokens = math.floor(input_tokens * reuse_ratio)
    reuse_tokens = reuse_tokens // BLOCK_SIZE * BLOCK_SIZE
    prepared = []
    for request_id, source_index in enumerate(order):
        row = dict(rows[source_index])
        input_ids = row["input_tok_ids"]
        if len(input_ids) < input_tokens:
            raise ValueError(
                f"Source request {source_index} has {len(input_ids)} input tokens; "
                f"required {input_tokens}"
            )
        arrival_ns += round(rng.expovariate(rate) * 1e9)
        row["request_id"] = request_id
        row["input_toks"] = input_tokens
        row["input_tok_ids"] = input_ids[:input_tokens]
        row["reuse_prefix_toks"] = reuse_tokens
        row["request_payload_bytes"] = 500 + input_tokens * 4
        row["arrival_time_ns"] = arrival_ns
        row["request_send_time_ns"] = arrival_ns
        prepared.append(row)
    return prepared


def main():
    with SOURCE.open(encoding="utf-8") as source:
        rows = [json.loads(line) for line in source if line.strip()]
    if len(rows) != 300:
        raise ValueError(f"Expected 300 source requests, found {len(rows)}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for input_tokens in INPUT_SIZES:
        for rate in REQUEST_RATES:
            for reuse_ratio in REUSE_RATIOS:
                for seed in SEEDS:
                    condition = condition_name(input_tokens, rate, reuse_ratio, seed)
                    output_path = OUTPUT_DIR / f"{condition}.jsonl"
                    prepared = prepare(rows, input_tokens, rate, reuse_ratio, seed)
                    with output_path.open("w", encoding="utf-8") as output:
                        for row in prepared:
                            output.write(json.dumps(row, separators=(",", ":")) + "\n")
                    manifest.append({
                        "condition": condition,
                        "input_tokens": input_tokens,
                        "request_rate_rps": rate,
                        "reuse_ratio": reuse_ratio,
                        "seed": seed,
                        "requests": len(prepared),
                        "last_arrival_ns": prepared[-1]["arrival_time_ns"],
                        "workload": str(output_path.relative_to(REPO_ROOT)),
                    })

    manifest_path = EXPERIMENT_DIR / "configs/phase1_manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(manifest[0])
    with manifest_path.open("w", encoding="utf-8") as output:
        output.write(",".join(columns) + "\n")
        for row in manifest:
            output.write(",".join(str(row[column]) for column in columns) + "\n")
    print(f"Prepared {len(manifest)} workloads in {OUTPUT_DIR}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
