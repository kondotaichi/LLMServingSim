#!/usr/bin/env python3
"""Remap a hongo_*.jsonl workload onto PP2 groups using the current GPU CSV."""

import argparse
import json
import math
from pathlib import Path

PP_GROUP_SIZE = 2


def pp_group_id(physical_gpu_id: int) -> int:
    return physical_gpu_id // PP_GROUP_SIZE


def group_positions(gpus_csv: Path) -> dict[int, tuple[float, float]]:
    """Physical GPU coordinates, keyed by physical gpu_id."""
    import csv
    positions = {}
    with gpus_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            positions[int(row["gpu_id"])] = (float(row["gpu_x_m"]), float(row["gpu_y_m"]))
    return positions


def nearest_other_group(
    row: dict,
    positions: dict[int, tuple[float, float]],
    num_pp_groups: int,
) -> tuple[int, float]:
    home_group = pp_group_id(int(row["gpu_id"]))
    user_x, user_y = float(row["user_x_m"]), float(row["user_y_m"])
    candidates = []
    for group_id in range(num_pp_groups):
        if group_id == home_group:
            continue
        distance = min(
            math.hypot(user_x - positions[gid][0], user_y - positions[gid][1])
            for gid in range(group_id * PP_GROUP_SIZE, (group_id + 1) * PP_GROUP_SIZE)
        )
        candidates.append((distance, group_id))
    distance, group_id = min(candidates)
    return group_id, distance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--gpus-csv", required=True)
    args = ap.parse_args()

    positions = group_positions(Path(args.gpus_csv))
    num_physical_gpus = len(positions)
    if num_physical_gpus % PP_GROUP_SIZE != 0:
        raise ValueError(
            f"Physical GPU count {num_physical_gpus} is not divisible by PP group size {PP_GROUP_SIZE}."
        )
    num_pp_groups = num_physical_gpus // PP_GROUP_SIZE

    rows = []
    with open(args.input) as f:
        for line in f:
            rows.append(json.loads(line))

    for row in rows:
        physical_home = int(row["gpu_id"])
        physical_second = int(row["second_nearest_gpu_id"])
        group_id = pp_group_id(physical_home)
        second_group_id, second_group_distance = nearest_other_group(row, positions, num_pp_groups)
        row.update({
            "physical_assigned_instance_id": physical_home,
            "physical_second_nearest_gpu_id": physical_second,
            "pp_group_id": group_id,
            "pp_stage_id": physical_home % PP_GROUP_SIZE,
            "assigned_instance_id": group_id,
            "second_nearest_gpu_id": second_group_id,
            "second_nearest_distance_m": second_group_distance,
        })

    with open(args.output, "w") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Wrote {len(rows)} requests -> {args.output} "
          f"({num_physical_gpus} physical GPUs -> {num_pp_groups} PP2 groups)")


if __name__ == "__main__":
    main()
