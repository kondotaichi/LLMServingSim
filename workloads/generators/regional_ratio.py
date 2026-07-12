"""Map a flat workload onto regional and temporal load ratios from a CSV."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


def register_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, help="Source flat JSONL workload.")
    parser.add_argument("--ratios", required=True, help="Regional 10-minute ratio CSV.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--users-per-region", type=int, default=2)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=86400.0,
        help="Compress the 24-hour regional timeline into this duration.",
    )
    parser.add_argument("--seed", type=int, default=42)


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    ratio_rows = []
    with Path(args.ratios).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["day_type"] != args.day_type:
                continue
            ratio_rows.append({
                "region": row["region_label"],
                "slot": int(row["local_10min_slot"]),
                "weight": float(row["avg_requests_per_active_user"]),
            })
    if not ratio_rows:
        raise ValueError(f"No ratio rows found for day_type={args.day_type!r}")

    regions = sorted({row["region"] for row in ratio_rows})
    region_id = {region: index for index, region in enumerate(regions)}
    weighted_slots = [(row, row["weight"]) for row in ratio_rows]
    choices = [item[0] for item in weighted_slots]
    weights = [item[1] for item in weighted_slots]

    source_rows = []
    with Path(args.input).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                source_rows.append(json.loads(line))

    output_rows = []
    user_cursor = defaultdict(int)
    for request_id, source in enumerate(source_rows):
        selected = rng.choices(choices, weights=weights, k=1)[0]
        rid = region_id[selected["region"]]
        local_user = user_cursor[rid] % args.users_per_region
        user_cursor[rid] += 1
        day_offset_ns = (
            selected["slot"] * 600 * 1_000_000_000
            + rng.randrange(600 * 1_000_000_000)
        )
        arrival_time_ns = int(day_offset_ns * args.duration_seconds / 86400.0)
        row = dict(source)
        row.update({
            "request_id": request_id,
            "user_id": rid * args.users_per_region + local_user,
            "region": selected["region"],
            "assigned_instance_id": rid,
            "gpu_id": rid,
            "local_10min_slot": selected["slot"],
            "arrival_time_ns": arrival_time_ns,
        })
        output_rows.append(row)

    output_rows.sort(key=lambda row: (row["arrival_time_ns"], row["request_id"]))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Wrote {len(output_rows)} requests across {len(regions)} regions -> {output_path}")
    return 0
