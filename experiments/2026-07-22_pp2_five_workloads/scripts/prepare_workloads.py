#!/usr/bin/env python3
"""Map ten geographically distributed GPUs into five two-stage PP groups."""

import csv
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = EXPERIMENT_DIR / "workloads"
PLACEMENT_DIR = EXPERIMENT_DIR / "placement"

CASES = (
    (
        "input512_reuse00",
        "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input512_reuse00.jsonl",
    ),
    (
        "input2000_reuse025",
        "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input2000_reuse025.jsonl",
    ),
    (
        "input6000_reuse05",
        "workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl",
    ),
    (
        "input10000_reuse00",
        "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl",
    ),
    (
        "input10000_reuse025",
        "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse025.jsonl",
    ),
    (
        "input10000_reuse05",
        "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse05.jsonl",
    ),
    (
        "mixed_rate3p33_seed1",
        "experiments/2026-07-21_mixed_workload_model_value/workloads/mixed_rate3p33_seed1.jsonl",
    ),
)

INPUT10000_SOURCE = "experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl"
INPUT8000_CONDITIONS = {
    "input8000_reuse00": 0,
    "input8000_reuse025": 2000,
    "input8000_reuse05": 4000,
}

PP_GROUP_SIZE = 2
NUM_PHYSICAL_GPUS = 10
NUM_PP_GROUPS = NUM_PHYSICAL_GPUS // PP_GROUP_SIZE


def pp_group_id(physical_gpu_id: int) -> int:
    if not 0 <= physical_gpu_id < NUM_PHYSICAL_GPUS:
        raise ValueError(f"physical GPU ID out of range: {physical_gpu_id}")
    return physical_gpu_id // PP_GROUP_SIZE


def gpu_coordinates(rows: list[dict]) -> dict[int, tuple[float, float]]:
    coordinates = {}
    for row in rows:
        gpu_id = int(row["gpu_id"])
        xy = (float(row["gpu_x_m"]), float(row["gpu_y_m"]))
        previous = coordinates.setdefault(gpu_id, xy)
        if previous != xy:
            raise ValueError(f"inconsistent coordinates for physical GPU {gpu_id}")
    if set(coordinates) != set(range(NUM_PHYSICAL_GPUS)):
        raise ValueError(f"expected physical GPU IDs 0..9, found {sorted(coordinates)}")
    return coordinates


def nearest_other_group(row: dict, coordinates: dict[int, tuple[float, float]]) -> tuple[int, float]:
    home_group = pp_group_id(int(row["gpu_id"]))
    user_x = float(row["user_x_m"])
    user_y = float(row["user_y_m"])
    candidates = []
    for group_id in range(NUM_PP_GROUPS):
        if group_id == home_group:
            continue
        distance = min(
            math.hypot(user_x - coordinates[gpu_id][0], user_y - coordinates[gpu_id][1])
            for gpu_id in range(group_id * PP_GROUP_SIZE, (group_id + 1) * PP_GROUP_SIZE)
        )
        candidates.append((distance, group_id))
    distance, group_id = min(candidates)
    return group_id, distance


def transform(rows: list[dict]) -> tuple[list[dict], dict[int, tuple[float, float]]]:
    coordinates = gpu_coordinates(rows)
    transformed = []
    for row in rows:
        physical_home = int(row["assigned_instance_id"])
        physical_second = int(row["second_nearest_gpu_id"])
        group_id = pp_group_id(physical_home)
        second_group_id, second_group_distance = nearest_other_group(row, coordinates)

        output = dict(row)
        output.update({
            "physical_assigned_instance_id": physical_home,
            "physical_second_nearest_gpu_id": physical_second,
            "pp_group_id": group_id,
            "pp_stage_id": physical_home % PP_GROUP_SIZE,
            "assigned_instance_id": group_id,
            "second_nearest_gpu_id": second_group_id,
            "second_nearest_distance_m": second_group_distance,
        })
        transformed.append(output)
    return transformed, coordinates


def write_placement(name: str, rows: list[dict], coordinates: dict[int, tuple[float, float]], source: str) -> None:
    users = {}
    for row in rows:
        user_id = int(row["user_id"])
        users.setdefault(user_id, row)

    with (PLACEMENT_DIR / f"{name}_users.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([
            "user_id", "user_x_m", "user_y_m", "physical_home_gpu_id",
            "pp_group_id", "pp_stage_id",
        ])
        for user_id, row in sorted(users.items()):
            writer.writerow([
                user_id, row["user_x_m"], row["user_y_m"],
                row["physical_assigned_instance_id"], row["pp_group_id"], row["pp_stage_id"],
            ])

    with (PLACEMENT_DIR / f"{name}_gpus.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["physical_gpu_id", "pp_group_id", "pp_stage_id", "gpu_x_m", "gpu_y_m"])
        for gpu_id, (gpu_x, gpu_y) in sorted(coordinates.items()):
            writer.writerow([gpu_id, pp_group_id(gpu_id), gpu_id % PP_GROUP_SIZE, gpu_x, gpu_y])

    metadata = {
        "source_workload": source,
        "output_workload": str((OUTPUT_DIR / f"{name}.jsonl").relative_to(REPO_ROOT)),
        "requests": len(rows),
        "physical_gpu_count": NUM_PHYSICAL_GPUS,
        "logical_pp_group_count": NUM_PP_GROUPS,
        "pp_group_size": PP_GROUP_SIZE,
        "pp_group_mapping": {
            str(group_id): [group_id * 2, group_id * 2 + 1]
            for group_id in range(NUM_PP_GROUPS)
        },
        "geographic_placement": "preserved from source workload",
        "transformed_fields": ["assigned_instance_id", "second_nearest_gpu_id", "second_nearest_distance_m"],
        "preserved_physical_fields": ["gpu_id", "gpu_x_m", "gpu_y_m", "user_x_m", "user_y_m", "distance_m"],
    }
    with (PLACEMENT_DIR / f"{name}_metadata.json").open("w", encoding="utf-8") as output:
        json.dump(metadata, output, indent=2)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PLACEMENT_DIR.mkdir(parents=True, exist_ok=True)
    cases = list(CASES)
    source_path = REPO_ROOT / INPUT10000_SOURCE
    with source_path.open(encoding="utf-8") as input_file:
        input8000_rows = [json.loads(line) for line in input_file if line.strip()]
    for name, source in cases:
        source_path = REPO_ROOT / source
        with source_path.open(encoding="utf-8") as input_file:
            source_rows = [json.loads(line) for line in input_file if line.strip()]
        rows, coordinates = transform(source_rows)
        with (OUTPUT_DIR / f"{name}.jsonl").open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, separators=(",", ":")) + "\n")
        write_placement(name, rows, coordinates, source)
        print(f"Prepared {name}: {len(rows)} requests, 10 physical GPUs -> 5 PP groups")

    for name, reuse_prefix_toks in INPUT8000_CONDITIONS.items():
        condition_rows = []
        for source_row in input8000_rows:
            row = dict(source_row)
            row["input_toks"] = 8000
            row["input_tok_ids"] = source_row["input_tok_ids"][:8000]
            row["request_payload_bytes"] = 32500
            row["reuse_prefix_toks"] = reuse_prefix_toks
            condition_rows.append(row)

        pp1_path = OUTPUT_DIR / f"{name}_pp1.jsonl"
        with pp1_path.open("w", encoding="utf-8") as output:
            for row in condition_rows:
                output.write(json.dumps(row, separators=(",", ":")) + "\n")

        rows, coordinates = transform(condition_rows)
        with (OUTPUT_DIR / f"{name}.jsonl").open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, separators=(",", ":")) + "\n")
        write_placement(name, rows, coordinates, INPUT10000_SOURCE)
        print(f"Prepared {name}: {len(rows)} requests, 10 physical GPUs -> 5 PP groups")


if __name__ == "__main__":
    main()
