#!/usr/bin/env python3
"""Build eight-GH200 placements and Peak 1x-10x repeat600 workloads."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXP_ROOT.parents[1]
SOURCE_ROOT = REPO_ROOT / "experiments/2026-08-01_hongo_workload"
SOURCE_WORKLOADS = SOURCE_ROOT / "workloads"
SOURCE_USERS = SOURCE_ROOT / "placements/hongo_users.csv"
PLACEMENTS = EXP_ROOT / "placements"
WORKLOADS = EXP_ROOT / "workloads"

CAMPUS_SIDE_M = math.sqrt(0.56) * 1000
GPU_COLS = 4
GPU_ROWS = 2
PP_GROUP_SIZE = 2
DISTANCE_LATENCY_NS_PER_M = 5.0
TOKEN_NAMESPACE_OFFSET = 1_000_000
LEVEL_SOURCES = {
    1: "busy_hour",
    2: "peak_2x",
    3: "peak_3x",
    4: "peak_4x",
    5: "peak_5x",
    6: "peak_6x",
    7: "peak_7x",
    8: "peak_8x",
    9: "peak_9x",
    10: "peak_10x",
}


def gpu_positions() -> list[dict]:
    positions = []
    for row in range(GPU_ROWS):
        for col in range(GPU_COLS):
            gpu_id = row * GPU_COLS + col
            positions.append({
                "gpu_id": gpu_id,
                "instance_id": gpu_id,
                "gpu_x_m": CAMPUS_SIDE_M * (col + 0.5) / GPU_COLS,
                "gpu_y_m": CAMPUS_SIDE_M * (row + 0.5) / GPU_ROWS,
                "zone": "campus",
            })
    return positions


def distance(x: float, y: float, position: dict) -> float:
    return math.hypot(x - position["gpu_x_m"], y - position["gpu_y_m"])


def nearest_physical(x: float, y: float, positions: list[dict]) -> list[tuple[float, int]]:
    return sorted((distance(x, y, position), int(position["gpu_id"])) for position in positions)


def group_distance(x: float, y: float, group_id: int, positions: list[dict]) -> float:
    start = group_id * PP_GROUP_SIZE
    return min(distance(x, y, positions[gpu_id]) for gpu_id in range(start, start + PP_GROUP_SIZE))


def remap_row(row: dict, positions: list[dict]) -> dict:
    remapped = dict(row)
    user_x = float(row["user_x_m"])
    user_y = float(row["user_y_m"])
    physical = nearest_physical(user_x, user_y, positions)
    nearest_distance, nearest_gpu = physical[0]
    second_distance, second_gpu = physical[1]
    throughput_mbps = float(row["network_throughput_mbps"])
    request_bytes = int(row["request_payload_bytes"])
    first_token_bytes = int(row["first_token_payload_bytes"])
    distance_ns = round(DISTANCE_LATENCY_NS_PER_M * nearest_distance)
    uplink_serialization_ns = round(8000.0 * request_bytes / throughput_mbps)
    downlink_serialization_ns = round(8000.0 * first_token_bytes / throughput_mbps)
    uplink_ns = distance_ns + uplink_serialization_ns
    downlink_ns = distance_ns + downlink_serialization_ns
    remapped.update({
        "gpu_id": nearest_gpu,
        "nearest_gpu_id": nearest_gpu,
        "assigned_instance_id": nearest_gpu,
        "gpu_x_m": positions[nearest_gpu]["gpu_x_m"],
        "gpu_y_m": positions[nearest_gpu]["gpu_y_m"],
        "distance_m": nearest_distance,
        "second_nearest_gpu_id": second_gpu,
        "second_nearest_distance_m": second_distance,
        "distance_latency_ns_per_meter": DISTANCE_LATENCY_NS_PER_M,
        "uplink_distance_latency_ns": distance_ns,
        "uplink_serialization_latency_ns": uplink_serialization_ns,
        "uplink_latency_ns": uplink_ns,
        "downlink_distance_latency_ns": distance_ns,
        "downlink_serialization_latency_ns": downlink_serialization_ns,
        "downlink_latency_ns": downlink_ns,
        "communication_latency_ns": uplink_ns + downlink_ns,
    })
    return remapped


def to_pp2(row: dict, positions: list[dict]) -> dict:
    transformed = dict(row)
    physical_home = int(row["gpu_id"])
    physical_second = int(row["second_nearest_gpu_id"])
    home_group = physical_home // PP_GROUP_SIZE
    candidates = sorted(
        (group_distance(float(row["user_x_m"]), float(row["user_y_m"]), group, positions), group)
        for group in range(len(positions) // PP_GROUP_SIZE)
        if group != home_group
    )
    second_group_distance, second_group = candidates[0]
    transformed.update({
        "physical_assigned_instance_id": physical_home,
        "physical_second_nearest_gpu_id": physical_second,
        "pp_group_id": home_group,
        "pp_stage_id": physical_home % PP_GROUP_SIZE,
        "assigned_instance_id": home_group,
        "second_nearest_gpu_id": second_group,
        "second_nearest_distance_m": second_group_distance,
    })
    return transformed


def repeat600(rows: list[dict]) -> list[dict]:
    if len(rows) != 300:
        raise ValueError(f"Expected 300 source requests, found {len(rows)}")
    rows = sorted(rows, key=lambda row: int(row["request_send_time_ns"]))
    first_send = int(rows[0]["request_send_time_ns"])
    last_send = int(rows[-1]["request_send_time_ns"])
    mean_interval = round((last_send - first_send) / (len(rows) - 1))
    period = last_send - first_send + mean_interval
    max_request_id = max(int(row["request_id"]) for row in rows)
    numeric_sessions = [int(row["session_id"]) for row in rows if isinstance(row.get("session_id"), int)]
    session_offset = max(numeric_sessions, default=0) + 1
    output = []
    for row in rows:
        original = dict(row)
        original["repeat_cycle"] = 1
        output.append(original)
    for row in rows:
        copied = dict(row)
        copied["request_id"] = int(row["request_id"]) + max_request_id + 1
        if isinstance(row.get("session_id"), int):
            copied["session_id"] = int(row["session_id"]) + session_offset
        elif "session_id" in row:
            copied["session_id"] = f'{row["session_id"]}_repeat'
        copied["request_send_time_ns"] = int(row["request_send_time_ns"]) + period
        copied["arrival_time_ns"] = int(row["arrival_time_ns"]) + period
        for key in ("input_tok_ids", "output_tok_ids"):
            if key in row:
                copied[key] = [int(token) + TOKEN_NAMESPACE_OFFSET for token in row[key]]
        copied["repeat_cycle"] = 2
        output.append(copied)
    return output


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def write_placements(positions: list[dict]) -> None:
    PLACEMENTS.mkdir(parents=True, exist_ok=True)
    with (PLACEMENTS / "hongo_gh200_gpus.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(positions[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(positions)

    with SOURCE_USERS.open(encoding="utf-8", newline="") as source:
        users = list(csv.DictReader(source))
    fields = list(users[0])
    with (PLACEMENTS / "hongo_users_8gpu.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for user in users:
            nearest = nearest_physical(float(user["user_x_m"]), float(user["user_y_m"]), positions)
            user["assigned_gpu_id"] = nearest[0][1]
            user["distance_to_gpu_m"] = nearest[0][0]
            user["second_nearest_gpu_id"] = nearest[1][1]
            user["second_nearest_distance_m"] = nearest[1][0]
            writer.writerow(user)


def validate(rows: list[dict], pp2: bool) -> None:
    if len(rows) != 600:
        raise ValueError(f"Expected 600 prepared requests, found {len(rows)}")
    instance_limit = 4 if pp2 else 8
    assigned = [int(row["assigned_instance_id"]) for row in rows]
    second = [int(row["second_nearest_gpu_id"]) for row in rows]
    if min(assigned + second) < 0 or max(assigned + second) >= instance_limit:
        raise ValueError(f"Instance ID outside [0, {instance_limit})")
    if any(a == b for a, b in zip(assigned, second)):
        raise ValueError("Nearest and second-nearest instance must differ")
    if any(int(row["request_send_time_ns"]) > int(rows[index + 1]["request_send_time_ns"])
           for index, row in enumerate(rows[:-1])):
        raise ValueError("Workload is not sorted by request_send_time_ns")


def main() -> None:
    positions = gpu_positions()
    write_placements(positions)
    WORKLOADS.mkdir(parents=True, exist_ok=True)
    manifest = []
    for level, source_label in LEVEL_SOURCES.items():
        source_path = SOURCE_WORKLOADS / f"hongo_{source_label}_seed1.jsonl"
        source_rows = [json.loads(line) for line in source_path.read_text().splitlines() if line.strip()]
        pp1_rows = repeat600([remap_row(row, positions) for row in source_rows])
        pp2_rows = [to_pp2(row, positions) for row in pp1_rows]
        validate(pp1_rows, pp2=False)
        validate(pp2_rows, pp2=True)
        pp1_path = WORKLOADS / f"hongo_peak_{level}x_repeat600_seed1.jsonl"
        pp2_path = WORKLOADS / f"hongo_peak_{level}x_repeat600_seed1_pp2.jsonl"
        write_jsonl(pp1_path, pp1_rows)
        write_jsonl(pp2_path, pp2_rows)
        span_s = (int(pp1_rows[-1]["request_send_time_ns"]) - int(pp1_rows[0]["request_send_time_ns"])) / 1e9
        rate_rps = (len(pp1_rows) - 1) / span_s
        counts = Counter(int(row["assigned_instance_id"]) for row in pp1_rows)
        manifest.append({
            "level": level,
            "source": source_path.relative_to(REPO_ROOT),
            "requests": len(pp1_rows),
            "span_s": f"{span_s:.9f}",
            "rate_rps": f"{rate_rps:.9f}",
            "min_requests_per_gpu": min(counts.values()),
            "max_requests_per_gpu": max(counts.values()),
            "pp1_workload": pp1_path.relative_to(EXP_ROOT),
            "pp2_workload": pp2_path.relative_to(EXP_ROOT),
        })
        print(f"Peak {level}x: 600 requests, {rate_rps:.3f} rps")
    with (EXP_ROOT / "configs/workload_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(manifest)


if __name__ == "__main__":
    main()
