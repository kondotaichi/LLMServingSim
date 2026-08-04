#!/usr/bin/env python3
"""Remap a hongo_*.jsonl workload's physical GPU IDs onto NUM_PHYSICAL_GPUS /
--pp-size logical PP groups (adjacent physical GPUs per group: group_id =
physical_id // pp_size). Generalizes
experiments/2026-08-01_hongo_workload/scripts/transform_pp2.py to arbitrary
pp-size.

--pp-size 1 is a no-op passthrough (kept so the sweep script can treat every
PP degree uniformly).
"""

import argparse
import csv
import json
import math
from pathlib import Path


def pp_group_id(physical_gpu_id: int, pp_size: int) -> int:
    return physical_gpu_id // pp_size


def group_positions(gpus_csv: Path) -> dict[int, tuple[float, float]]:
    """Physical GPU coordinates, keyed by physical gpu_id."""
    positions = {}
    with gpus_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            positions[int(row["gpu_id"])] = (float(row["gpu_x_m"]), float(row["gpu_y_m"]))
    return positions


def nearest_other_group(
    row: dict, positions: dict[int, tuple[float, float]], pp_size: int, num_groups: int
) -> tuple[int, float]:
    home_group = pp_group_id(int(row["gpu_id"]), pp_size)
    user_x, user_y = float(row["user_x_m"]), float(row["user_y_m"])
    candidates = []
    for group_id in range(num_groups):
        if group_id == home_group:
            continue
        distance = min(
            math.hypot(user_x - positions[gid][0], user_y - positions[gid][1])
            for gid in range(group_id * pp_size, (group_id + 1) * pp_size)
        )
        candidates.append((distance, group_id))
    distance, group_id = min(candidates)
    return group_id, distance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--gpus-csv", required=True)
    ap.add_argument("--pp-size", type=int, required=True)
    args = ap.parse_args()

    positions = group_positions(Path(args.gpus_csv))
    num_physical_gpus = len(positions)

    if num_physical_gpus % args.pp_size != 0:
        raise ValueError(
            f"pp-size {args.pp_size} must divide NUM_PHYSICAL_GPUS ({num_physical_gpus})"
        )
    num_groups = num_physical_gpus // args.pp_size

    rows = []
    with open(args.input) as f:
        for line in f:
            rows.append(json.loads(line))

    for row in rows:
        physical_home = int(row["gpu_id"])
        physical_second = int(row["second_nearest_gpu_id"])
        group_id = pp_group_id(physical_home, args.pp_size)
        second_group_id, second_group_distance = nearest_other_group(
            row, positions, args.pp_size, num_groups
        )
        row.update({
            "physical_assigned_instance_id": physical_home,
            "physical_second_nearest_gpu_id": physical_second,
            "pp_group_id": group_id,
            "pp_stage_id": physical_home % args.pp_size,
            "assigned_instance_id": group_id,
            "second_nearest_gpu_id": second_group_id,
            "second_nearest_distance_m": second_group_distance,
        })

    with open(args.output, "w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(
        f"Wrote {len(rows)} requests -> {args.output} "
        f"({num_physical_gpus} physical GPUs -> {num_groups} PP{args.pp_size} groups)"
    )


if __name__ == "__main__":
    main()
