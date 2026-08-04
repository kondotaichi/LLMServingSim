#!/usr/bin/env python3
"""Generate repeated-user, KV-pressure workloads for Method C evaluation."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
PLACEMENT_DIR = EXPERIMENT_DIR / "placements"
WORKLOAD_DIR = EXPERIMENT_DIR / "workloads"

NUM_INSTANCES = 6
USERS_PER_INSTANCE = 4
INPUT_TOKENS = 8_000
OUTPUT_TOKENS = 256
REUSE_PREFIX_TOKENS = 2_000
NUM_REQUESTS = 300
SEED = 1

NETWORK_THROUGHPUT_MBPS = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES = 500
BYTES_PER_INPUT_TOKEN = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100
KV_MIGRATION_BANDWIDTH_GBPS = 10.7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", type=float, nargs="+", default=[4.0, 5.0, 6.0])
    parser.add_argument("--num-reqs", type=int, default=NUM_REQUESTS)
    parser.add_argument("--seed", type=int, default=SEED)
    return parser.parse_args()


def rate_label(rate: float) -> str:
    text = str(int(rate)) if rate.is_integer() else str(rate).replace(".", "p")
    return f"rate{text}"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_balanced_users(rows: list[dict]) -> dict[int, list[dict]]:
    by_instance: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("is_daily_active", "True").lower() not in ("true", "1"):
            continue
        instance_id = int(row["assigned_gpu_id"]) // 2
        by_instance[instance_id].append(row)

    selected = {}
    for instance_id in range(NUM_INSTANCES):
        candidates = sorted(by_instance[instance_id], key=lambda row: int(row["user_id"]))
        if len(candidates) < USERS_PER_INSTANCE:
            raise ValueError(f"instance {instance_id} has only {len(candidates)} active users")
        selected[instance_id] = candidates[:USERS_PER_INSTANCE]
    return selected


def poisson_arrivals(rng: random.Random, rate_rps: float, n: int) -> list[int]:
    intervals = [rng.expovariate(rate_rps) for _ in range(n)]
    scale = (n / rate_rps) / sum(intervals)
    current = 0.0
    arrivals = []
    for interval in intervals:
        current += interval * scale
        arrivals.append(round(current * 1e9))
    return arrivals


def serialization_ns(payload_bytes: int) -> int:
    return round(8_000.0 * payload_bytes / NETWORK_THROUGHPUT_MBPS)


def request_slots(rng: random.Random, n: int) -> list[tuple[int, int]]:
    result = []
    base, remainder = divmod(n, NUM_INSTANCES)
    for instance_id in range(NUM_INSTANCES):
        count = base + (1 if instance_id < remainder else 0)
        offsets = [index % USERS_PER_INSTANCE for index in range(count)]
        rng.shuffle(offsets)
        result.extend((instance_id, user_offset) for user_offset in offsets)
    rng.shuffle(result)
    return result


def build_workload(
    placement_name: str,
    rate_rps: float,
    num_requests: int,
    seed: int,
) -> list[dict]:
    users = select_balanced_users(read_csv(PLACEMENT_DIR / f"{placement_name}_users.csv"))
    gpus = {
        int(row["gpu_id"]): row
        for row in read_csv(PLACEMENT_DIR / f"{placement_name}_gpus.csv")
    }
    rng = random.Random(seed * 1_000_003 + round(rate_rps * 1_000))
    send_times = poisson_arrivals(rng, rate_rps, num_requests)
    slots = request_slots(rng, num_requests)
    payload_bytes = PROTOCOL_OVERHEAD_BYTES + INPUT_TOKENS * BYTES_PER_INPUT_TOKEN
    uplink_serialization_ns = serialization_ns(payload_bytes)
    downlink_serialization_ns = serialization_ns(FIRST_TOKEN_PAYLOAD_BYTES)

    rows = []
    for request_id, (send_time_ns, slot) in enumerate(zip(send_times, slots)):
        instance_id, user_offset = slot
        user = users[instance_id][user_offset]
        gpu_id = int(user["assigned_gpu_id"])
        gpu = gpus[gpu_id]
        distance_m = float(user["distance_to_gpu_m"])
        uplink_distance_ns = round(DISTANCE_LATENCY_NS_PER_M * distance_m)
        downlink_distance_ns = uplink_distance_ns
        uplink_ns = uplink_distance_ns + uplink_serialization_ns
        downlink_ns = downlink_distance_ns + downlink_serialization_ns
        rows.append({
            "request_id": request_id,
            "input_toks": INPUT_TOKENS,
            "output_toks": OUTPUT_TOKENS,
            "arrival_time_ns": send_time_ns + uplink_ns,
            "request_send_time_ns": send_time_ns,
            "reuse_prefix_toks": REUSE_PREFIX_TOKENS,
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
            "workload_level": f"pressure_{rate_label(rate_rps)}",
            "kv_migration_bandwidth_gbps": KV_MIGRATION_BANDWIDTH_GBPS,
            "kv_migration_distance_m": float(user["second_nearest_distance_m"]),
        })

    rows.sort(key=lambda row: (row["arrival_time_ns"], row["request_id"]))
    for request_id, row in enumerate(rows):
        row["request_id"] = request_id
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    args = parse_args()
    layouts = {
        "all_tokyo_pp2": "all_tokyo",
        "kagoshima_tokyo_pp2": "kagoshima_tokyo",
    }
    for rate in args.rates:
        label = rate_label(rate)
        for output_dir, placement_name in layouts.items():
            rows = build_workload(placement_name, rate, args.num_reqs, args.seed)
            path = WORKLOAD_DIR / output_dir / f"pressure_{label}_seed{args.seed}.jsonl"
            write_jsonl(path, rows)
            span_s = (rows[-1]["request_send_time_ns"] - rows[0]["request_send_time_ns"]) / 1e9
            print(f"{path}: n={len(rows)} span={span_s:.2f}s rate={(len(rows)-1)/span_s:.2f} req/s")


if __name__ == "__main__":
    main()
