#!/usr/bin/env python3
"""Build the campus-only Hongo workload with 12 fixed physical GPUs.

This generator aligns the experiment assets with report/design.md:
- single campus zone only
- 27,000 users
- 12 physical RTX4090 GPUs laid out in a 3x4 grid
- PP2 comparisons use the same 12 physical GPUs, exposed as 6 logical groups
"""

from __future__ import annotations

import argparse
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

PREFIX = "hongo"

# --- Geography / population (report/design.md) ---
CAMPUS_AREA_KM2 = 0.56
SIDE_M = math.sqrt(CAMPUS_AREA_KM2) * 1000.0
CAMPUS_POPULATION = 27_000

# --- GPU placement: 12 physical GPUs, fixed across PP1/PP2 comparisons ---
GPU_ROWS = 3
GPU_COLS = 4
NUM_PHYSICAL_GPUS = GPU_ROWS * GPU_COLS
PP_GROUP_SIZE = 2
NUM_PP_GROUPS = NUM_PHYSICAL_GPUS // PP_GROUP_SIZE

PLACEMENT_SEED = 20_260_801

# --- Segment-specific activity assumptions ---
SEGMENTS = {
    "campus": {
        "population": CAMPUS_POPULATION,
        "dau_fraction": 0.20,
        "requests_per_active_user_day": 40.0,
    }
}
BUSY_HOUR_DAILY_SHARE = 0.15
PEAK_MULTIPLIER = 2.0
SEEDS = (1,)

NETWORK_THROUGHPUT_MBPS = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES = 500
BYTES_PER_INPUT_TOKEN = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100
BLOCK_SIZE = 16
KV_REUSE_RATIO = 0.5


def request_rates() -> dict[str, float]:
    daily_requests = sum(
        seg["population"] * seg["dau_fraction"] * seg["requests_per_active_user_day"]
        for seg in SEGMENTS.values()
    )
    busy_hour = daily_requests * BUSY_HOUR_DAILY_SHARE / 3_600.0
    return {
        "daily_average": daily_requests / 86_400.0,
        "busy_hour": busy_hour,
        "peak_2x": busy_hour * PEAK_MULTIPLIER,
    }


def peak_label(multiplier: float) -> str:
    if float(multiplier).is_integer():
        text = str(int(multiplier))
    else:
        text = str(multiplier).replace(".", "p")
    return f"peak_{text}x"


def compression_from_peak2x(multiplier: float) -> float:
    return PEAK_MULTIPLIER / multiplier


def gpu_positions() -> list[dict]:
    positions = []
    for row in range(GPU_ROWS):
        for col in range(GPU_COLS):
            positions.append(
                {
                    "gpu_x_m": (col + 0.5) * SIDE_M / GPU_COLS,
                    "gpu_y_m": (row + 0.5) * SIDE_M / GPU_ROWS,
                    "zone": "campus",
                }
            )
    for gpu_id, entry in enumerate(positions):
        entry["gpu_id"] = gpu_id
    return positions


def nearest_two(xy: tuple[float, float], positions: list[dict]) -> tuple[int, float, int, float]:
    ux, uy = xy
    ranked = sorted(
        (math.hypot(ux - p["gpu_x_m"], uy - p["gpu_y_m"]), p["gpu_id"])
        for p in positions
    )
    return ranked[0][1], ranked[0][0], ranked[1][1], ranked[1][0]


def create_placements(positions: list[dict]) -> list[dict]:
    rng = random.Random(PLACEMENT_SEED)
    users = []
    for user_id in range(CAMPUS_POPULATION):
        xy = (rng.uniform(0.0, SIDE_M), rng.uniform(0.0, SIDE_M))
        nearest_id, dist, second_id, second_dist = nearest_two(xy, positions)
        users.append(
            {
                "user_id": user_id,
                "segment": "campus",
                "user_x_m": xy[0],
                "user_y_m": xy[1],
                "request_weight": 1.0,
                "request_probability": 1.0 / CAMPUS_POPULATION,
                "assigned_gpu_id": nearest_id,
                "distance_to_gpu_m": dist,
                "second_nearest_gpu_id": second_id,
                "second_nearest_distance_m": second_dist,
            }
        )
    return users


def write_placements(positions: list[dict], users: list[dict]) -> None:
    PLACEMENT_DIR.mkdir(parents=True, exist_ok=True)
    with (PLACEMENT_DIR / f"{PREFIX}_gpus.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["gpu_id", "instance_id", "gpu_x_m", "gpu_y_m", "zone"])
        for p in positions:
            writer.writerow([p["gpu_id"], p["gpu_id"], p["gpu_x_m"], p["gpu_y_m"], p["zone"]])

    with (PLACEMENT_DIR / f"{PREFIX}_users.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [k for k in users[0] if k != "segment"] + ["segment"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(users)


def cluster_config(num_instances: int) -> dict:
    nodes = []
    for _ in range(num_instances):
        nodes.append(
            {
                "num_instances": 1,
                "cpu_mem": {"mem_size": 128, "mem_bw": 33.8, "mem_latency": 102.9},
                "instances": [
                    {
                        "model_name": "meta-llama/Llama-3.1-8B",
                        "hardware": "RTX4090",
                        "npu_mem": {"mem_size": 24, "mem_bw": 1_008, "mem_latency": 0},
                        "pd_type": None,
                        "num_npus": 1,
                        "tp_size": 1,
                        "pp_size": 1,
                    }
                ],
            }
        )
    return {"num_nodes": num_instances, "link_bw": 16, "link_latency": 20_000, "nodes": nodes}


def write_cluster_configs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / f"rtx4090_{PREFIX}.json").write_text(
        json.dumps(cluster_config(NUM_PHYSICAL_GPUS), indent=2) + "\n",
        encoding="utf-8",
    )
    (CONFIG_DIR / f"rtx4090_{PREFIX}_pp2.json").write_text(
        json.dumps(cluster_config(NUM_PP_GROUPS), indent=2) + "\n",
        encoding="utf-8",
    )


def serialization_ns(payload_bytes: int) -> int:
    return round(8_000.0 * payload_bytes / NETWORK_THROUGHPUT_MBPS)


def load_session_content(path: Path) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if any("session_id" not in row for row in rows):
        raise ValueError(f"{path} is missing session_id.")
    return rows


def assign_users(content_rows: list[dict], users: list[dict], seed: int) -> dict[int, dict]:
    rng = random.Random(seed)
    pool = list(range(len(users)))
    rng.shuffle(pool)
    session_user: dict[int, dict] = {}
    next_pool_idx = 0
    for row in content_rows:
        sid = row["session_id"]
        if sid in session_user:
            continue
        if next_pool_idx >= len(pool):
            next_pool_idx = 0
        session_user[sid] = users[pool[next_pool_idx]]
        next_pool_idx += 1
    return session_user


def arrivals_cumulative(rng: random.Random, rate_rps: float, n: int) -> list[int]:
    intervals = [rng.expovariate(rate_rps) for _ in range(n)]
    target_duration_s = n / rate_rps
    scale = target_duration_s / sum(intervals)
    current_s = 0.0
    result = []
    for interval_s in intervals:
        current_s += interval_s * scale
        result.append(round(current_s * 1e9))
    return result


def build_workload(
    label: str,
    rate_rps: float,
    seed: int,
    positions: list[dict],
    content_rows: list[dict],
    session_user: dict[int, dict],
) -> tuple[list[dict], dict]:
    rng = random.Random(seed * 1_000_003 + round(rate_rps * 1_000))
    n = len(content_rows)
    send_times = arrivals_cumulative(rng, rate_rps, n)
    rows = []
    input_counts = []
    gpu_counts = Counter()
    for content_row, send_time_ns in zip(content_rows, send_times):
        sid = content_row["session_id"]
        user = session_user[sid]
        gpu_id = user["assigned_gpu_id"]
        gpu = positions[gpu_id]
        input_toks = content_row["input_toks"]
        output_toks = content_row["output_toks"]
        reuse_prefix_toks = int(input_toks * KV_REUSE_RATIO) // BLOCK_SIZE * BLOCK_SIZE
        payload_bytes = PROTOCOL_OVERHEAD_BYTES + input_toks * BYTES_PER_INPUT_TOKEN
        uplink_distance_ns = round(DISTANCE_LATENCY_NS_PER_M * user["distance_to_gpu_m"])
        uplink_serialization_ns = serialization_ns(payload_bytes)
        uplink_ns = uplink_distance_ns + uplink_serialization_ns
        downlink_distance_ns = uplink_distance_ns
        downlink_serialization_ns = serialization_ns(FIRST_TOKEN_PAYLOAD_BYTES)
        downlink_ns = downlink_distance_ns + downlink_serialization_ns

        rows.append(
            {
                "request_id": len(rows),
                "session_id": sid,
                "segment": user["segment"],
                "input_toks": input_toks,
                "output_toks": output_toks,
                "input_tok_ids": content_row["input_tok_ids"],
                "output_tok_ids": content_row["output_tok_ids"],
                "arrival_time_ns": send_time_ns + uplink_ns,
                "request_send_time_ns": send_time_ns,
                "reuse_prefix_toks": reuse_prefix_toks,
                "user_id": user["user_id"],
                "user_x_m": user["user_x_m"],
                "user_y_m": user["user_y_m"],
                "gpu_id": gpu_id,
                "nearest_gpu_id": gpu_id,
                "assigned_instance_id": gpu_id,
                "gpu_x_m": gpu["gpu_x_m"],
                "gpu_y_m": gpu["gpu_y_m"],
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
            }
        )
        input_counts.append(input_toks)
        gpu_counts[gpu_id] += 1

    unique_sessions = len({r["session_id"] for r in rows})
    manifest = {
        "condition": f"{label}_seed{seed}",
        "load_level": label,
        "seed": seed,
        "requests": n,
        "unique_sessions": unique_sessions,
        "return_visit_requests": n - unique_sessions,
        "return_visit_rate": (n - unique_sessions) / n,
        "target_rate_rps": rate_rps,
        "duration_s": send_times[-1] / 1e9,
        "mean_input_toks": sum(input_counts) / n,
        "campus_requests": n,
        "resident_requests": 0,
        "min_requests_per_gpu": min(gpu_counts.values()),
        "max_requests_per_gpu": max(gpu_counts.values()),
        "workload": f"workloads/{PREFIX}_{label}_seed{seed}.jsonl",
    }
    return rows, manifest


def compress_workload(rows: list[dict], factor: float, label: str) -> list[dict]:
    compressed = []
    for row in rows:
        new_row = dict(row)
        send_time_ns = round(int(row["request_send_time_ns"]) * factor)
        uplink_ns = int(row["uplink_latency_ns"])
        new_row["request_send_time_ns"] = send_time_ns
        new_row["arrival_time_ns"] = send_time_ns + uplink_ns
        new_row["workload_level"] = label
        compressed.append(new_row)
    return compressed


def pp_group_id(physical_gpu_id: int) -> int:
    return physical_gpu_id // PP_GROUP_SIZE


def nearest_other_group(row: dict, positions: dict[int, tuple[float, float]]) -> tuple[int, float]:
    home_group = pp_group_id(int(row["gpu_id"]))
    user_x, user_y = float(row["user_x_m"]), float(row["user_y_m"])
    candidates = []
    for group_id in range(NUM_PP_GROUPS):
        if group_id == home_group:
            continue
        distance = min(
            math.hypot(user_x - positions[gid][0], user_y - positions[gid][1])
            for gid in range(group_id * PP_GROUP_SIZE, (group_id + 1) * PP_GROUP_SIZE)
        )
        candidates.append((distance, group_id))
    distance, group_id = min(candidates)
    return group_id, distance


def transform_pp2_rows(rows: list[dict], positions: list[dict]) -> list[dict]:
    pos_map = {int(p["gpu_id"]): (float(p["gpu_x_m"]), float(p["gpu_y_m"])) for p in positions}
    transformed = []
    for row in rows:
        physical_home = int(row["gpu_id"])
        physical_second = int(row["second_nearest_gpu_id"])
        group_id = pp_group_id(physical_home)
        second_group_id, second_group_distance = nearest_other_group(row, pos_map)
        new_row = dict(row)
        new_row.update(
            {
                "physical_assigned_instance_id": physical_home,
                "physical_second_nearest_gpu_id": physical_second,
                "pp_group_id": group_id,
                "pp_stage_id": physical_home % PP_GROUP_SIZE,
                "assigned_instance_id": group_id,
                "second_nearest_gpu_id": second_group_id,
                "second_nearest_distance_m": second_group_distance,
            }
        )
        transformed.append(new_row)
    return transformed


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--content",
        required=True,
        help="Session content source. Can be sharegpt.py output or an existing hongo_*.jsonl workload.",
    )
    ap.add_argument(
        "--num-reqs",
        type=int,
        default=2000,
        help="Number of requests to keep from the content source.",
    )
    ap.add_argument(
        "--derived-peak-multipliers",
        type=float,
        nargs="*",
        default=(3.0,),
        help=(
            "Additional busy-hour multipliers derived by compressing the peak_2x "
            "timeline. For example: 3 -> peak_3x, 4 -> peak_4x."
        ),
    )
    args = ap.parse_args()

    content_rows = load_session_content(Path(args.content))
    if args.num_reqs < 1:
        raise ValueError("--num-reqs must be positive.")
    if len(content_rows) < args.num_reqs:
        raise ValueError(
            f"{args.content} only has {len(content_rows)} rows, need {args.num_reqs}."
        )
    content_rows = content_rows[: args.num_reqs]
    derived_peak_multipliers = sorted(
        {
            float(multiplier)
            for multiplier in args.derived_peak_multipliers
            if float(multiplier) > PEAK_MULTIPLIER
        }
    )
    positions = gpu_positions()
    users = create_placements(positions)
    write_placements(positions, users)
    write_cluster_configs()
    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)

    manifests = []
    built_rows: dict[str, list[dict]] = {}
    for label, rate_rps in request_rates().items():
        for seed in SEEDS:
            session_user = assign_users(content_rows, users, seed=PLACEMENT_SEED + seed)
            rows, manifest = build_workload(label, rate_rps, seed, positions, content_rows, session_user)
            output_path = WORKLOAD_DIR / f"{PREFIX}_{label}_seed{seed}.jsonl"
            write_jsonl(output_path, rows)
            manifests.append(manifest)
            built_rows[label] = rows

    peak2_rate = request_rates()["peak_2x"]
    for seed in SEEDS:
        peak2_pp2 = transform_pp2_rows(built_rows["peak_2x"], positions)
        write_jsonl(WORKLOAD_DIR / f"{PREFIX}_peak_2x_seed{seed}_pp2.jsonl", peak2_pp2)

        for multiplier in derived_peak_multipliers:
            label = peak_label(multiplier)
            factor = compression_from_peak2x(multiplier)
            rows = compress_workload(built_rows["peak_2x"], factor, label)
            write_jsonl(WORKLOAD_DIR / f"{PREFIX}_{label}_seed{seed}.jsonl", rows)
            manifests.append(
                {
                    "condition": f"{label}_seed{seed}",
                    "load_level": label,
                    "seed": seed,
                    "requests": len(rows),
                    "unique_sessions": len({r["session_id"] for r in rows}),
                    "return_visit_requests": len(rows) - len({r["session_id"] for r in rows}),
                    "return_visit_rate": (len(rows) - len({r["session_id"] for r in rows})) / len(rows),
                    "target_rate_rps": peak2_rate / factor,
                    "duration_s": rows[-1]["request_send_time_ns"] / 1e9,
                    "mean_input_toks": sum(r["input_toks"] for r in rows) / len(rows),
                    "campus_requests": len(rows),
                    "resident_requests": 0,
                    "min_requests_per_gpu": min(Counter(r["gpu_id"] for r in rows).values()),
                    "max_requests_per_gpu": max(Counter(r["gpu_id"] for r in rows).values()),
                    "workload": f"workloads/{PREFIX}_{label}_seed{seed}.jsonl",
                }
            )
            rows_pp2 = transform_pp2_rows(rows, positions)
            write_jsonl(WORKLOAD_DIR / f"{PREFIX}_{label}_seed{seed}_pp2.jsonl", rows_pp2)

    with (CONFIG_DIR / f"{PREFIX}_workload_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifests[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(manifests)

    print(f"Wrote {len(users):,} campus users and {len(positions)} fixed physical GPUs")
    print(f"PP2 grouping: {NUM_PHYSICAL_GPUS} physical GPUs -> {NUM_PP_GROUPS} logical groups")
    for manifest in manifests:
        print(
            f"  {manifest['condition']}: {manifest['requests']:,} requests, "
            f"duration={manifest['duration_s']:.0f}s, "
            f"campus={manifest['campus_requests']}, resident={manifest['resident_requests']}"
        )


if __name__ == "__main__":
    main()
