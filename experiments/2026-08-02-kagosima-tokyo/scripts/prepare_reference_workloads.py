#!/usr/bin/env python3
"""Map the proven 2026-07-28 workload onto the Tokyo/Kagoshima layouts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
PLACEMENT_DIR = EXPERIMENT_DIR / "placements"
WORKLOAD_DIR = EXPERIMENT_DIR / "workloads"
DEFAULT_SOURCE = (
    REPO_ROOT
    / "experiments/2026-07-22_pp2_five_workloads/workloads/input8000_reuse025.jsonl"
)

NETWORK_THROUGHPUT_MBPS = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES = 500
BYTES_PER_INPUT_TOKEN = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--rates", type=float, nargs="+", default=[4.0, 5.0, 6.0])
    parser.add_argument("--num-reqs", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args()


def read_jsonl(path: Path, limit: int) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for _, line in zip(range(limit), handle)]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rate_label(rate: float) -> str:
    return str(int(rate)) if rate.is_integer() else str(rate).replace(".", "p")


def assign_source_users(rows: list[dict], num_instances: int) -> dict[int, int]:
    counts = Counter(int(row["user_id"]) for row in rows)
    loads = [0] * num_instances
    mapping = {}
    for user_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        instance_id = min(range(num_instances), key=lambda index: (loads[index], index))
        mapping[user_id] = instance_id
        loads[instance_id] += count
    return mapping


def select_layout_users(placement_name: str, needed: Counter, pp_size: int) -> dict[int, list[dict]]:
    candidates: dict[int, list[dict]] = defaultdict(list)
    for row in read_csv(PLACEMENT_DIR / f"{placement_name}_users.csv"):
        if row.get("is_daily_active", "True").lower() not in ("true", "1"):
            continue
        candidates[int(row["assigned_gpu_id"]) // pp_size].append(row)
    selected = {}
    for instance_id, count in needed.items():
        pool = sorted(candidates[instance_id], key=lambda row: int(row["user_id"]))
        if len(pool) < count:
            raise ValueError(f"instance {instance_id} needs {count} users, found {len(pool)}")
        selected[instance_id] = pool[:count]
    return selected


def serialization_ns(payload_bytes: int) -> int:
    return round(8_000.0 * payload_bytes / NETWORK_THROUGHPUT_MBPS)


def transform(
    rows: list[dict], placement_name: str, rate: Optional[float], pp_size: int
) -> list[dict]:
    num_instances = 12 // pp_size
    source_to_instance = assign_source_users(rows, num_instances)
    source_users_by_instance: dict[int, list[int]] = defaultdict(list)
    for source_user, instance_id in sorted(source_to_instance.items()):
        source_users_by_instance[instance_id].append(source_user)
    needed = Counter({key: len(value) for key, value in source_users_by_instance.items()})
    layout_users = select_layout_users(placement_name, needed, pp_size)
    user_mapping = {}
    for instance_id, source_users in source_users_by_instance.items():
        for source_user, layout_user in zip(source_users, layout_users[instance_id]):
            user_mapping[source_user] = layout_user

    gpus = {
        int(row["gpu_id"]): row
        for row in read_csv(PLACEMENT_DIR / f"{placement_name}_gpus.csv")
    }
    original_times = [int(row.get("request_send_time_ns", row["arrival_time_ns"])) for row in rows]
    original_start = original_times[0]
    original_span = original_times[-1] - original_start
    target_span = original_span if rate is None else round((len(rows) - 1) / rate * 1e9)

    transformed = []
    for request_id, (source, original_time) in enumerate(zip(rows, original_times)):
        row = dict(source)
        source_user = int(source["user_id"])
        user = user_mapping[source_user]
        instance_id = source_to_instance[source_user]
        gpu_id = int(user["assigned_gpu_id"])
        gpu = gpus[gpu_id]
        send_time_ns = round((original_time - original_start) * target_span / original_span)
        input_tokens = int(row["input_toks"])
        payload_bytes = PROTOCOL_OVERHEAD_BYTES + input_tokens * BYTES_PER_INPUT_TOKEN
        distance_m = float(user["distance_to_gpu_m"])
        uplink_distance_ns = round(DISTANCE_LATENCY_NS_PER_M * distance_m)
        uplink_serialization_ns = serialization_ns(payload_bytes)
        downlink_distance_ns = uplink_distance_ns
        downlink_serialization_ns = serialization_ns(FIRST_TOKEN_PAYLOAD_BYTES)
        uplink_ns = uplink_distance_ns + uplink_serialization_ns
        downlink_ns = downlink_distance_ns + downlink_serialization_ns
        row.update({
            "request_id": request_id,
            "request_send_time_ns": send_time_ns,
            "arrival_time_ns": send_time_ns + uplink_ns,
            "user_id": int(user["user_id"]),
            "user_x_m": float(user["user_x_m"]),
            "user_y_m": float(user["user_y_m"]),
            "region": user.get("region", "tokyo"),
            "gpu_id": gpu_id,
            "nearest_gpu_id": gpu_id,
            "assigned_instance_id": instance_id,
            "second_nearest_gpu_id": int(user["second_nearest_gpu_id"]),
            "second_nearest_distance_m": float(user["second_nearest_distance_m"]),
            "gpu_x_m": float(gpu["gpu_x_m"]),
            "gpu_y_m": float(gpu["gpu_y_m"]),
            "distance_m": distance_m,
            "network_throughput_mbps": NETWORK_THROUGHPUT_MBPS,
            "distance_latency_ns_per_meter": DISTANCE_LATENCY_NS_PER_M,
            "request_payload_bytes": payload_bytes,
            "first_token_payload_bytes": FIRST_TOKEN_PAYLOAD_BYTES,
            "uplink_distance_latency_ns": uplink_distance_ns,
            "uplink_serialization_latency_ns": uplink_serialization_ns,
            "uplink_latency_ns": uplink_ns,
            "downlink_distance_latency_ns": downlink_distance_ns,
            "downlink_serialization_latency_ns": downlink_serialization_ns,
            "downlink_latency_ns": downlink_ns,
            "communication_latency_ns": uplink_ns + downlink_ns,
            "workload_level": "reference_original" if rate is None else f"reference_rate{rate_label(rate)}",
            "kv_migration_bandwidth_gbps": 10.7,
            "kv_migration_distance_m": float(user["second_nearest_distance_m"]),
        })
        transformed.append(row)
    transformed.sort(key=lambda row: (row["arrival_time_ns"], row["request_id"]))
    for request_id, row in enumerate(transformed):
        row["request_id"] = request_id
    return transformed


def main() -> None:
    args = parse_args()
    source = read_jsonl(args.source, args.num_reqs)
    layouts = {
        "all_tokyo_pp2": ("all_tokyo", 2),
        "kagoshima_tokyo_pp2": ("kagoshima_tokyo", 2),
        "all_tokyo_pp1": ("all_tokyo", 1),
        "kagoshima_tokyo_pp1": ("kagoshima_tokyo", 1),
    }
    variants = [(None, "original")] + [(rate, f"rate{rate_label(rate)}") for rate in args.rates]
    for rate, label in variants:
        for output_dir, (placement_name, pp_size) in layouts.items():
            rows = transform(source, placement_name, rate, pp_size)
            path = WORKLOAD_DIR / output_dir / f"reference_{label}_seed{args.seed}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            print(f"{path}: {len(rows)} requests")


if __name__ == "__main__":
    main()
