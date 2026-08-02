#!/usr/bin/env python3
"""Build the Hongo (Bunkyo-ku) workload: real ShareGPT session content laid
over a two-zone geography (UTokyo campus + residential) with role-segmented
daily-active-user rates. See report/design.md for the full derivation.

Usage:
    python3 -m workloads.generators sharegpt \\
        --model meta-llama/Llama-3.1-8B --source shibing624/sharegpt_gpt4 \\
        --num-reqs 2000 --sps 10 --seed 42 \\
        --min-input-toks 3000 --max-input-toks 20000 --max-kv-toks 40000 \\
        --min-output-toks 0 --max-sessions 0 \\
        --output experiments/2026-08-01_hongo_workload/workloads/sharegpt_2k_deep_session.jsonl

    python3 experiments/2026-08-01_hongo_workload/scripts/prepare_hongo_workload.py \\
        --content experiments/2026-08-01_hongo_workload/workloads/sharegpt_2k_deep_session.jsonl
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

# --- Geography (report/design.md section 2) ---
TOTAL_AREA_KM2 = 1.363
SIDE_M = math.sqrt(TOTAL_AREA_KM2) * 1000.0          # ~1167.5 m
CAMPUS_AREA_KM2 = 0.56
CAMPUS_WIDTH_M = CAMPUS_AREA_KM2 * 1_000_000.0 / SIDE_M   # ~479.6 m
RESIDENTIAL_WIDTH_M = SIDE_M - CAMPUS_WIDTH_M              # ~687.9 m

# --- Population (section 1) ---
RESIDENTIAL_POPULATION = 22_259
CAMPUS_POPULATION = 27_000  # 21,000 students + 3,963 faculty + ~2,000 staff (estimate)

# --- GPU allocation (section 3): population-proportional (~13:11), rounded
# to two clean 3x4 grids for simplicity. ---
CAMPUS_GPU_ROWS, CAMPUS_GPU_COLS = 3, 4
RESIDENTIAL_GPU_ROWS, RESIDENTIAL_GPU_COLS = 3, 4
NUM_GPUS = CAMPUS_GPU_ROWS * CAMPUS_GPU_COLS + RESIDENTIAL_GPU_ROWS * RESIDENTIAL_GPU_COLS  # 24

PLACEMENT_SEED = 20_260_801

# --- Segment-specific daily-active-user rates (section 5) ---
SEGMENTS = {
    "resident": {"population": RESIDENTIAL_POPULATION, "dau_fraction": 0.10, "requests_per_active_user_day": 20.0},
    "campus": {"population": CAMPUS_POPULATION, "dau_fraction": 0.20, "requests_per_active_user_day": 20.0},
}
BUSY_HOUR_DAILY_SHARE = 0.15
PEAK_MULTIPLIER = 2.0
SEEDS = (1,)  # start with one seed; extend later

NETWORK_THROUGHPUT_MBPS = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES = 500
BYTES_PER_INPUT_TOKEN = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100
BLOCK_SIZE = 16
KV_REUSE_RATIO = 0.5  # declared reuse_prefix_toks label for Method A/B's cost model


def request_rates() -> dict[str, float]:
    daily_requests = sum(
        seg["population"] * seg["dau_fraction"] * seg["requests_per_active_user_day"]
        for seg in SEGMENTS.values()
    )
    return {
        "daily_average": daily_requests / 86_400.0,
        "busy_hour": daily_requests * BUSY_HOUR_DAILY_SHARE / 3_600.0,
        "peak_2x": daily_requests * BUSY_HOUR_DAILY_SHARE / 3_600.0 * PEAK_MULTIPLIER,
    }


def gpu_positions() -> list[dict]:
    """Return [{gpu_id, gpu_x_m, gpu_y_m, zone}], campus block first (x in
    [0, CAMPUS_WIDTH_M]), residential block second (x in [CAMPUS_WIDTH_M,
    SIDE_M])."""
    positions = []
    for row in range(CAMPUS_GPU_ROWS):
        for col in range(CAMPUS_GPU_COLS):
            positions.append({
                "gpu_x_m": (col + 0.5) * CAMPUS_WIDTH_M / CAMPUS_GPU_COLS,
                "gpu_y_m": (row + 0.5) * SIDE_M / CAMPUS_GPU_ROWS,
                "zone": "campus",
            })
    for row in range(RESIDENTIAL_GPU_ROWS):
        for col in range(RESIDENTIAL_GPU_COLS):
            positions.append({
                "gpu_x_m": CAMPUS_WIDTH_M + (col + 0.5) * RESIDENTIAL_WIDTH_M / RESIDENTIAL_GPU_COLS,
                "gpu_y_m": (row + 0.5) * SIDE_M / RESIDENTIAL_GPU_ROWS,
                "zone": "residential",
            })
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
    user_id = 0
    zone_bounds = {
        "campus": (0.0, CAMPUS_WIDTH_M, RESIDENTIAL_POPULATION >= 0 and CAMPUS_POPULATION),
        "residential": (CAMPUS_WIDTH_M, SIDE_M, RESIDENTIAL_POPULATION),
    }
    for segment_name, seg in SEGMENTS.items():
        zone = "campus" if segment_name == "campus" else "residential"
        x_min, x_max, n = zone_bounds[zone]
        n = int(seg["population"])
        for _ in range(n):
            xy = (rng.uniform(x_min, x_max), rng.uniform(0.0, SIDE_M))
            nearest_id, dist, second_id, second_dist = nearest_two(xy, positions)
            users.append({
                "user_id": user_id,
                "segment": segment_name,
                "user_x_m": xy[0],
                "user_y_m": xy[1],
                "request_weight": 1.0,
                "request_probability": 1.0 / n,
                "assigned_gpu_id": nearest_id,
                "distance_to_gpu_m": dist,
                "second_nearest_gpu_id": second_id,
                "second_nearest_distance_m": second_dist,
            })
            user_id += 1
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


def cluster_config(hardware: str) -> dict:
    nodes = []
    for _ in range(NUM_GPUS):
        nodes.append({
            "num_instances": 1,
            "cpu_mem": {"mem_size": 128, "mem_bw": 33.8, "mem_latency": 102.9},
            "instances": [{
                "model_name": "meta-llama/Llama-3.1-8B",
                "hardware": hardware,
                "npu_mem": {"mem_size": 24, "mem_bw": 1_008, "mem_latency": 0},
                "pd_type": None,
                "num_npus": 1,
                "tp_size": 1,
                "pp_size": 1,
            }],
        })
    return {"num_nodes": NUM_GPUS, "link_bw": 16, "link_latency": 20_000, "nodes": nodes}


def write_cluster_config() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"rtx4090_{PREFIX}.json"
    path.write_text(json.dumps(cluster_config("RTX4090"), indent=2) + "\n", encoding="utf-8")


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
        raise ValueError(
            f"{path} is missing session_id -- generate it via "
            "`python -m workloads.generators sharegpt` (not --fix-len)"
        )
    return rows


def assign_users(content_rows: list[dict], users: list[dict], seed: int) -> dict[int, dict]:
    """Pin each session_id to one user (drawn uniformly across the *whole*
    population -- residents and campus users mixed -- without replacement
    while the pool lasts), so all of a session's turns share one location."""
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
    """Cumulative Poisson process, in the *given order* -- callers must feed
    rows already in a causally-valid order (sharegpt.py's file order already
    keeps each session's own turns increasing) since this never reorders."""
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
    segment_counts = Counter()
    for (content_row, send_time_ns) in zip(content_rows, send_times):
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
        rows.append({
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
        })
        input_counts.append(input_toks)
        gpu_counts[gpu_id] += 1
        segment_counts[user["segment"]] += 1
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
        "campus_requests": segment_counts["campus"],
        "resident_requests": segment_counts["resident"],
        "min_requests_per_gpu": min(gpu_counts.values()),
        "max_requests_per_gpu": max(gpu_counts.values()),
        "workload": f"workloads/{PREFIX}_{label}_seed{seed}.jsonl",
    }
    return rows, manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--content", required=True,
        help="Path to sharegpt.py output with session_id (e.g. "
             "workloads/sharegpt_2k_deep_session.jsonl)",
    )
    args = ap.parse_args()

    content_rows = load_session_content(Path(args.content))
    positions = gpu_positions()
    users = create_placements(positions)
    write_placements(positions, users)
    write_cluster_config()
    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)

    manifests = []
    for label, rate_rps in request_rates().items():
        for seed in SEEDS:
            session_user = assign_users(content_rows, users, seed=PLACEMENT_SEED + seed)
            rows, manifest = build_workload(
                label, rate_rps, seed, positions, content_rows, session_user
            )
            output_path = WORKLOAD_DIR / f"{PREFIX}_{label}_seed{seed}.jsonl"
            with output_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            manifests.append(manifest)

    with (CONFIG_DIR / f"{PREFIX}_workload_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifests[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(manifests)

    print(f"Wrote {len(users):,} users ({CAMPUS_POPULATION:,} campus + "
          f"{RESIDENTIAL_POPULATION:,} resident) and {len(positions)} GPUs "
          f"({TOTAL_AREA_KM2} km^2)")
    print(f"Content pool: {len(content_rows):,} requests, "
          f"{len({r['session_id'] for r in content_rows}):,} unique sessions")
    for manifest in manifests:
        print(f"  {manifest['condition']}: {manifest['requests']:,} requests, "
              f"return-visit rate={manifest['return_visit_rate']*100:.1f}%, "
              f"duration={manifest['duration_s']:.0f}s, "
              f"campus={manifest['campus_requests']}, resident={manifest['resident_requests']}")


if __name__ == "__main__":
    main()
