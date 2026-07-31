#!/usr/bin/env python3
"""Build a reproducible urban workload for a 20-GPU deployment."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = EXPERIMENT_DIR / "configs"
PLACEMENT_DIR = EXPERIMENT_DIR / "placements"
WORKLOAD_DIR = EXPERIMENT_DIR / "workloads"

AREA_KM2 = 11.0
AREA_SIDE_M = math.sqrt(AREA_KM2) * 1000.0
POPULATION = 200_000
GPU_ROWS = 4
GPU_COLS = 5
NUM_GPUS = GPU_ROWS * GPU_COLS

DAU_FRACTION = 0.10
NUM_DAILY_ACTIVE_USERS = round(POPULATION * DAU_FRACTION)
REQUESTS_PER_ACTIVE_USER_DAY = 20.0
BUSY_HOUR_DAILY_SHARE = 0.15
PEAK_MULTIPLIER = 2.0
NUM_REQUESTS = 3_000
SEEDS = (1, 2, 3)
PLACEMENT_SEED = 20_260_731

NETWORK_THROUGHPUT_MBPS = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES = 500
BYTES_PER_INPUT_TOKEN = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100

INPUT_TOKEN_DISTRIBUTION = (
    (512, 0.20),
    (2_000, 0.25),
    (4_000, 0.25),
    (6_000, 0.15),
    (8_000, 0.10),
    (10_000, 0.05),
)
OUTPUT_TOKEN_DISTRIBUTION = (
    (128, 0.30),
    (256, 0.40),
    (512, 0.20),
    (1_024, 0.10),
)


def request_rates() -> dict[str, float]:
    daily_requests = POPULATION * DAU_FRACTION * REQUESTS_PER_ACTIVE_USER_DAY
    return {
        "daily_average": daily_requests / 86_400.0,
        "busy_hour": daily_requests * BUSY_HOUR_DAILY_SHARE / 3_600.0,
        "peak_2x": daily_requests * BUSY_HOUR_DAILY_SHARE / 3_600.0 * PEAK_MULTIPLIER,
    }


def gpu_positions() -> list[tuple[float, float]]:
    return [
        (
            (column + 0.5) * AREA_SIDE_M / GPU_COLS,
            (row + 0.5) * AREA_SIDE_M / GPU_ROWS,
        )
        for row in range(GPU_ROWS)
        for column in range(GPU_COLS)
    ]


def nearest_two(
    user_xy: tuple[float, float],
    positions: list[tuple[float, float]],
) -> tuple[int, float, int, float]:
    ux, uy = user_xy
    ranked = sorted(
        (math.hypot(ux - gx, uy - gy), gpu_id)
        for gpu_id, (gx, gy) in enumerate(positions)
    )
    return ranked[0][1], ranked[0][0], ranked[1][1], ranked[1][0]


def create_placements() -> tuple[list[tuple[float, float]], list[dict]]:
    rng = random.Random(PLACEMENT_SEED)
    active_rng = random.Random(PLACEMENT_SEED + 1)
    active_user_ids = set(active_rng.sample(range(POPULATION), NUM_DAILY_ACTIVE_USERS))
    positions = gpu_positions()
    users = []
    nearest_counts = Counter()
    for user_id in range(POPULATION):
        user_xy = (
            rng.uniform(0.0, AREA_SIDE_M),
            rng.uniform(0.0, AREA_SIDE_M),
        )
        nearest_id, distance_m, second_id, second_distance_m = nearest_two(
            user_xy, positions
        )
        nearest_counts[nearest_id] += 1
        users.append({
            "user_id": user_id,
            "user_x_m": user_xy[0],
            "user_y_m": user_xy[1],
            "request_weight": 1.0 if user_id in active_user_ids else 0.0,
            "request_probability": (
                1.0 / NUM_DAILY_ACTIVE_USERS if user_id in active_user_ids else 0.0
            ),
            "is_daily_active": user_id in active_user_ids,
            "assigned_gpu_id": nearest_id,
            "distance_to_gpu_m": distance_m,
            "second_nearest_gpu_id": second_id,
            "second_nearest_distance_m": second_distance_m,
        })
    if set(nearest_counts) != set(range(NUM_GPUS)):
        raise ValueError("Every GPU must receive at least one nearest user")
    return positions, users


def write_placements(positions: list[tuple[float, float]], users: list[dict]) -> None:
    PLACEMENT_DIR.mkdir(parents=True, exist_ok=True)
    with (PLACEMENT_DIR / "gpus.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gpu_id", "instance_id", "gpu_x_m", "gpu_y_m"])
        for gpu_id, (gpu_x, gpu_y) in enumerate(positions):
            writer.writerow([gpu_id, gpu_id, gpu_x, gpu_y])
    with (PLACEMENT_DIR / "users.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(users[0]))
        writer.writeheader()
        writer.writerows(users)


def weighted_choice(rng: random.Random, distribution: tuple[tuple[int, float], ...]) -> int:
    return rng.choices(
        [value for value, _ in distribution],
        weights=[weight for _, weight in distribution],
        k=1,
    )[0]


def arrivals(rng: random.Random, rate_rps: float) -> list[int]:
    intervals = [rng.expovariate(rate_rps) for _ in range(NUM_REQUESTS)]
    target_duration_s = NUM_REQUESTS / rate_rps
    scale = target_duration_s / sum(intervals)
    current_s = 0.0
    result = []
    for interval_s in intervals:
        current_s += interval_s * scale
        result.append(round(current_s * 1e9))
    return result


def serialization_ns(payload_bytes: int) -> int:
    return round(8_000.0 * payload_bytes / NETWORK_THROUGHPUT_MBPS)


def build_workload(
    label: str,
    rate_rps: float,
    seed: int,
    positions: list[tuple[float, float]],
    users: list[dict],
    active_users: list[dict],
) -> tuple[list[dict], dict]:
    rng = random.Random(seed * 1_000_003 + round(rate_rps * 1_000))
    send_times = arrivals(rng, rate_rps)
    rows = []
    input_counts = Counter()
    output_counts = Counter()
    gpu_counts = Counter()
    for request_id, send_time_ns in enumerate(send_times):
        user = active_users[rng.randrange(len(active_users))]
        user_id = user["user_id"]
        gpu_id = user["assigned_gpu_id"]
        gpu_x_m, gpu_y_m = positions[gpu_id]
        input_toks = weighted_choice(rng, INPUT_TOKEN_DISTRIBUTION)
        output_toks = weighted_choice(rng, OUTPUT_TOKEN_DISTRIBUTION)
        payload_bytes = PROTOCOL_OVERHEAD_BYTES + input_toks * BYTES_PER_INPUT_TOKEN
        uplink_distance_ns = round(
            DISTANCE_LATENCY_NS_PER_M * user["distance_to_gpu_m"]
        )
        uplink_serialization_ns = serialization_ns(payload_bytes)
        uplink_ns = uplink_distance_ns + uplink_serialization_ns
        downlink_distance_ns = uplink_distance_ns
        downlink_serialization_ns = serialization_ns(FIRST_TOKEN_PAYLOAD_BYTES)
        downlink_ns = downlink_distance_ns + downlink_serialization_ns
        rows.append({
            "request_id": request_id,
            "input_toks": input_toks,
            "output_toks": output_toks,
            "arrival_time_ns": send_time_ns + uplink_ns,
            "request_send_time_ns": send_time_ns,
            "reuse_prefix_toks": 0,
            "user_id": user_id,
            "user_x_m": user["user_x_m"],
            "user_y_m": user["user_y_m"],
            "gpu_id": gpu_id,
            "nearest_gpu_id": gpu_id,
            "assigned_instance_id": gpu_id,
            "gpu_x_m": gpu_x_m,
            "gpu_y_m": gpu_y_m,
            "distance_m": user["distance_to_gpu_m"],
            "second_nearest_gpu_id": user["second_nearest_gpu_id"],
            "second_nearest_distance_m": user["second_nearest_distance_m"],
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
            "workload_level": label,
        })
        input_counts[input_toks] += 1
        output_counts[output_toks] += 1
        gpu_counts[gpu_id] += 1
    realized_rate = NUM_REQUESTS / (send_times[-1] / 1e9)
    if not math.isclose(realized_rate, rate_rps, rel_tol=1e-9):
        raise ValueError(f"Realized rate {realized_rate} != target {rate_rps}")
    if any(
        left["arrival_time_ns"] >= right["arrival_time_ns"]
        for left, right in zip(rows, rows[1:])
    ):
        rows.sort(key=lambda row: (row["arrival_time_ns"], row["request_id"]))
        for request_id, row in enumerate(rows):
            row["request_id"] = request_id
    manifest = {
        "condition": f"{label}_seed{seed}",
        "load_level": label,
        "seed": seed,
        "requests": NUM_REQUESTS,
        "target_rate_rps": rate_rps,
        "realized_send_rate_rps": realized_rate,
        "duration_s": send_times[-1] / 1e9,
        "mean_input_toks": sum(row["input_toks"] for row in rows) / NUM_REQUESTS,
        "mean_output_toks": sum(row["output_toks"] for row in rows) / NUM_REQUESTS,
        "min_requests_per_gpu": min(gpu_counts.values()),
        "max_requests_per_gpu": max(gpu_counts.values()),
        "workload": f"workloads/{label}_seed{seed}.jsonl",
        "input_distribution": dict(sorted(input_counts.items())),
        "output_distribution": dict(sorted(output_counts.items())),
    }
    return rows, manifest


def cluster_config(hardware: str) -> dict:
    nodes = []
    for _ in range(NUM_GPUS):
        nodes.append({
            "num_instances": 1,
            "cpu_mem": {
                "mem_size": 480,
                "mem_bw": 500,
                "mem_latency": 0,
            },
            "instances": [{
                "model_name": "meta-llama/Llama-3.1-8B",
                "hardware": hardware,
                "npu_mem": {
                    "mem_size": 96,
                    "mem_bw": 4_000,
                    "mem_latency": 0,
                },
                "pd_type": None,
                "num_npus": 1,
                "tp_size": 1,
                "pp_size": 1,
            }],
        })
    return {
        "num_nodes": NUM_GPUS,
        "link_bw": 50,
        "link_latency": 20_000,
        "nodes": nodes,
    }


def write_cluster_configs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "gh200_class_20gpu_proxy.json": "RTXPRO6000",
        "gh200_20gpu.json": "GH200",
    }
    for filename, hardware in outputs.items():
        path = CONFIG_DIR / filename
        path.write_text(
            json.dumps(cluster_config(hardware), indent=2) + "\n",
            encoding="utf-8",
        )


def write_metadata(manifests: list[dict], users: list[dict]) -> None:
    nearest_counts = Counter(user["assigned_gpu_id"] for user in users)
    rates = request_rates()
    daily_requests = POPULATION * DAU_FRACTION * REQUESTS_PER_ACTIVE_USER_DAY
    metadata = {
        "geography": {
            "area_km2": AREA_KM2,
            "shape": "square",
            "side_m": AREA_SIDE_M,
            "population": POPULATION,
            "population_density_per_km2": POPULATION / AREA_KM2,
            "placement": "uniform random users, 4x5 regular GPU grid",
            "placement_seed": PLACEMENT_SEED,
        },
        "capacity": {
            "num_gpus": NUM_GPUS,
            "population_per_gpu": POPULATION / NUM_GPUS,
            "area_per_gpu_km2": AREA_KM2 / NUM_GPUS,
            "min_nearest_users_per_gpu": min(nearest_counts.values()),
            "max_nearest_users_per_gpu": max(nearest_counts.values()),
        },
        "traffic_model": {
            "daily_active_user_fraction": DAU_FRACTION,
            "daily_active_users": POPULATION * DAU_FRACTION,
            "requests_per_active_user_day": REQUESTS_PER_ACTIVE_USER_DAY,
            "total_requests_day": daily_requests,
            "busy_hour_daily_request_share": BUSY_HOUR_DAILY_SHARE,
            "peak_multiplier_over_busy_hour": PEAK_MULTIPLIER,
            "rates_rps": rates,
            "requests_per_trace": NUM_REQUESTS,
            "arrival_process": "Poisson, rescaled to exact target duration",
            "request_user_sampling": "uniform over the fixed daily-active user set",
            "prefix_reuse": "disabled in this baseline workload",
        },
        "network_model": {
            "access_throughput_mbps": NETWORK_THROUGHPUT_MBPS,
            "distance_latency_ns_per_meter": DISTANCE_LATENCY_NS_PER_M,
            "protocol_overhead_bytes": PROTOCOL_OVERHEAD_BYTES,
            "bytes_per_input_token": BYTES_PER_INPUT_TOKEN,
            "first_token_payload_bytes": FIRST_TOKEN_PAYLOAD_BYTES,
            "gpu_backbone_bandwidth_GBps": 50,
            "gpu_backbone_latency_ns": 20_000,
        },
        "hardware_model": {
            "target_class": "NVIDIA GH200 Grace Hopper, 96 GB HBM3 variant",
            "gpu_memory_GB": 96,
            "gpu_memory_bandwidth_GBps": 4_000,
            "grace_cpu_memory_GB": 480,
            "grace_cpu_memory_bandwidth_GBps": 500,
            "compute_profile_proxy": "RTXPRO6000",
            "warning": (
                "The repository has no GH200 profiler/perf dataset. Memory values are "
                "GH200-class, but compute latency uses the existing RTXPRO6000 profile."
            ),
        },
        "workloads": manifests,
    }
    (CONFIG_DIR / "experiment.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    positions, users = create_placements()
    active_users = [user for user in users if user["is_daily_active"]]
    if len(active_users) != NUM_DAILY_ACTIVE_USERS:
        raise ValueError("Unexpected daily-active user count")
    write_placements(positions, users)
    write_cluster_configs()
    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)
    manifests = []
    for label, rate_rps in request_rates().items():
        for seed in SEEDS:
            rows, manifest = build_workload(
                label, rate_rps, seed, positions, users, active_users
            )
            output_path = WORKLOAD_DIR / f"{label}_seed{seed}.jsonl"
            with output_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            manifests.append(manifest)
    with (CONFIG_DIR / "workload_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        flat_manifests = [
            {key: value for key, value in row.items() if not key.endswith("distribution")}
            for row in manifests
        ]
        writer = csv.DictWriter(handle, fieldnames=list(flat_manifests[0]))
        writer.writeheader()
        writer.writerows(flat_manifests)
    write_metadata(manifests, users)
    print(f"Wrote {len(users):,} users and {len(positions)} GPUs")
    print(f"Wrote {len(manifests)} workloads of {NUM_REQUESTS:,} requests")


if __name__ == "__main__":
    main()
