#!/usr/bin/env python3
"""
Workload generator for Kagoshima-Tokyo GPU deployment experiment.

Cluster layout
--------------
  12 GPUs total: 6 in Tokyo (GPU 0-5) + 6 in Kagoshima (GPU 6-11)
  Tokyo GPUs    : 2x3 grid inside a 1 km^2 Tokyo area
  Kagoshima GPUs: same 2x3 grid layout, offset 1,050,000 m in x (virtual coordinate)

User assignment
---------------
  All 20,000 users are physically in the Tokyo area.
  Users with even user_id  -> Tokyo GPU pool   (GPU 0-5)
  Users with odd  user_id  -> Kagoshima GPU pool (GPU 6-11)
  Within each pool the user is assigned to the nearest GPU in that pool.
  second_nearest_gpu_id is set to the nearest GPU in the OPPOSITE pool,
  enabling cross-region redirect for NEAREST_MIGRATE / NEAREST_MIGRATE_KV.

PP variants
-----------
  PP=1 (methods 1-3): assigned_instance_id = gpu_id        (12 instances, 0-11)
  PP=2 (methods 4-5): assigned_instance_id = gpu_id // 2   (6 instances, 0-5)

All-Tokyo baseline
------------------
  12 Tokyo GPUs only, users assigned purely by nearest geographic GPU.
  second_nearest_gpu_id = nearest OTHER Tokyo GPU.
  PP=2: assigned_instance_id = nearest_gpu_id // 2.

Outputs
-------
  placements/kagoshima_tokyo_gpus.csv
  placements/kagoshima_tokyo_users.csv
  placements/all_tokyo_gpus.csv
  placements/all_tokyo_users.csv
  workloads/kagoshima_tokyo_pp1/{daily_average,busy_hour,peak_2x}_seed1.jsonl
  workloads/kagoshima_tokyo_pp2/{daily_average,busy_hour,peak_2x}_seed1.jsonl
  workloads/all_tokyo_pp2/{daily_average,busy_hour,peak_2x}_seed1.jsonl
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
PLACEMENT_DIR = EXPERIMENT_DIR / "placements"
WORKLOAD_DIR  = EXPERIMENT_DIR / "workloads"

# ── geography ──────────────────────────────────────────────────────────────
AREA_SIDE_M        = 1_000.0        # Tokyo area 1 km²
KAGOSHIMA_OFFSET_M = 1_050_000.0   # ~1,050 km one-way propagation distance

# Tokyo GPU grid: 2 rows × 3 cols
TOKYO_GPU_XY = [
    (167.0, 333.0), (500.0, 333.0), (833.0, 333.0),
    (167.0, 667.0), (500.0, 667.0), (833.0, 667.0),
]
# Kagoshima GPU grid: same layout, offset in x
KAGOSHIMA_GPU_XY = [
    (KAGOSHIMA_OFFSET_M + x, y) for x, y in TOKYO_GPU_XY
]
ALL_GPU_XY = TOKYO_GPU_XY + KAGOSHIMA_GPU_XY   # indices 0-5 Tokyo, 6-11 Kagoshima

# All-Tokyo baseline: 12 GPUs in two 2×3 grids side-by-side inside the same 1 km² area
# Place them in a 2×6 layout: 2 rows × 6 cols (columns spaced 143 m)
ALL_TOKYO_GPU_XY = [
    (83.0 + col * 167.0, 333.0) for col in range(6)
] + [
    (83.0 + col * 167.0, 667.0) for col in range(6)
]

# ── population ─────────────────────────────────────────────────────────────
POPULATION           = 20_000
DAU_FRACTION         = 0.10
REQUESTS_PER_DAU_DAY = 20.0
BUSY_HOUR_SHARE      = 0.15
PEAK_MULTIPLIER      = 2.0
NUM_REQUESTS         = 360
SEED                 = 1
PLACEMENT_SEED       = 20_260_802

# ── access network ─────────────────────────────────────────────────────────
NETWORK_THROUGHPUT_MBPS   = 100.0
DISTANCE_LATENCY_NS_PER_M = 5.0
PROTOCOL_OVERHEAD_BYTES   = 500
BYTES_PER_INPUT_TOKEN     = 4
FIRST_TOKEN_PAYLOAD_BYTES = 100

INPUT_TOKEN_DIST = ((512, 0.20), (2_000, 0.25), (4_000, 0.25),
                    (6_000, 0.15), (8_000, 0.10), (10_000, 0.05))
OUTPUT_TOKEN_DIST = ((128, 0.30), (256, 0.40), (512, 0.20), (1_024, 0.10))

# ── prefix reuse (random model) ────────────────────────────────────────────
# REUSE_FRACTION of heavy requests are assigned a cached prefix drawn from
# REUSE_PREFIX_DIST, simulating users who have a shared system prompt or
# prior session context already on their home instance.
REUSE_FRACTION   = 0.25
REUSE_PREFIX_DIST = ((512, 0.40), (1_024, 0.30), (2_048, 0.20), (4_096, 0.10))

NUM_REQUESTS_HEAVY = 1_000


# ── helpers ────────────────────────────────────────────────────────────────

def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def nearest_in_pool(
    user_xy: tuple[float, float],
    pool_positions: list[tuple[float, float]],
    pool_offset: int,
) -> tuple[int, float]:
    ranked = sorted((dist_m(user_xy, p), i) for i, p in enumerate(pool_positions))
    local_idx = ranked[0][1]
    return pool_offset + local_idx, ranked[0][0]


def serialization_ns(payload_bytes: int) -> int:
    return round(8_000.0 * payload_bytes / NETWORK_THROUGHPUT_MBPS)


def weighted_choice(rng: random.Random, dist: tuple) -> int:
    return rng.choices([v for v, _ in dist], weights=[w for _, w in dist], k=1)[0]


def poisson_arrivals(rng: random.Random, rate_rps: float, n: int) -> list[int]:
    intervals = [rng.expovariate(rate_rps) for _ in range(n)]
    scale = (n / rate_rps) / sum(intervals)
    t = 0.0
    times = []
    for iv in intervals:
        t += iv * scale
        times.append(round(t * 1e9))
    return times


def request_rates() -> dict[str, float]:
    daily = POPULATION * DAU_FRACTION * REQUESTS_PER_DAU_DAY
    busy  = daily * BUSY_HOUR_SHARE / 3_600.0
    return {
        "daily_average": daily / 86_400.0,
        "busy_hour":     busy,
        "peak_2x":       busy * PEAK_MULTIPLIER,
        "heavy":         busy * 20.0,
    }


# ── placement ──────────────────────────────────────────────────────────────

def build_kagoshima_tokyo_users() -> list[dict]:
    rng = random.Random(PLACEMENT_SEED)
    act_rng = random.Random(PLACEMENT_SEED + 1)
    active_ids = set(act_rng.sample(range(POPULATION), round(POPULATION * DAU_FRACTION)))
    users = []
    for uid in range(POPULATION):
        xy = (rng.uniform(0.0, AREA_SIDE_M), rng.uniform(0.0, AREA_SIDE_M))
        # Assign 50/50 based on user_id parity.
        # Tokyo pool: geographic nearest (6 GPUs spread across 1 km² give natural balance).
        # Kagoshima pool: round-robin by uid because all Kagoshima GPUs appear equidistant
        # from any Tokyo user (~1,050 km), so geographic nearest would always pick the
        # leftmost GPU and starve the others.
        if uid % 2 == 0:
            gpu_id, d = nearest_in_pool(xy, TOKYO_GPU_XY, 0)
            sec_gpu_id, sec_d = 6 + (uid // 2) % 6, KAGOSHIMA_OFFSET_M
        else:
            local_idx = (uid // 2) % 6
            gpu_id    = 6 + local_idx
            d         = dist_m(xy, KAGOSHIMA_GPU_XY[local_idx])
            sec_gpu_id, sec_d = nearest_in_pool(xy, TOKYO_GPU_XY, 0)
        is_active = uid in active_ids
        users.append({
            "user_id": uid,
            "user_x_m": xy[0], "user_y_m": xy[1],
            "request_weight": 1.0 if is_active else 0.0,
            "request_probability": (1.0 / round(POPULATION * DAU_FRACTION)) if is_active else 0.0,
            "is_daily_active": is_active,
            "region": "tokyo" if uid % 2 == 0 else "kagoshima",
            "assigned_gpu_id": gpu_id,
            "distance_to_gpu_m": d,
            "second_nearest_gpu_id": sec_gpu_id,
            "second_nearest_distance_m": sec_d,
        })
    return users


def build_all_tokyo_users() -> list[dict]:
    rng = random.Random(PLACEMENT_SEED)
    act_rng = random.Random(PLACEMENT_SEED + 1)
    active_ids = set(act_rng.sample(range(POPULATION), round(POPULATION * DAU_FRACTION)))
    users = []
    for uid in range(POPULATION):
        xy = (rng.uniform(0.0, AREA_SIDE_M), rng.uniform(0.0, AREA_SIDE_M))
        ranked = sorted((dist_m(xy, p), i) for i, p in enumerate(ALL_TOKYO_GPU_XY))
        gpu_id, d = ranked[0][1], ranked[0][0]
        sec_gpu_id, sec_d = ranked[1][1], ranked[1][0]
        is_active = uid in active_ids
        users.append({
            "user_id": uid,
            "user_x_m": xy[0], "user_y_m": xy[1],
            "request_weight": 1.0 if is_active else 0.0,
            "request_probability": (1.0 / round(POPULATION * DAU_FRACTION)) if is_active else 0.0,
            "is_daily_active": is_active,
            "region": "tokyo",
            "assigned_gpu_id": gpu_id,
            "distance_to_gpu_m": d,
            "second_nearest_gpu_id": sec_gpu_id,
            "second_nearest_distance_m": sec_d,
        })
    return users


def write_gpus_csv(path: Path, gpu_positions: list[tuple[float, float]], labels: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["gpu_id", "instance_id", "gpu_x_m", "gpu_y_m", "region"])
        for i, (x, y) in enumerate(gpu_positions):
            w.writerow([i, i, x, y, labels[i]])


def write_users_csv(path: Path, users: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(users[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(users)


# ── workload generation ────────────────────────────────────────────────────

def build_workload(
    label: str,
    rate_rps: float,
    seed: int,
    gpu_positions: list[tuple[float, float]],
    users: list[dict],
    pp_size: int,
    reuse_fraction: float = 0.0,
    num_requests: int | None = None,
) -> list[dict]:
    active = [u for u in users if u["is_daily_active"]]
    n_reqs = num_requests if num_requests is not None else NUM_REQUESTS
    rng = random.Random(seed * 1_000_003 + round(rate_rps * 1_000))
    send_times = poisson_arrivals(rng, rate_rps, n_reqs)
    rows = []
    for req_id, send_ns in enumerate(send_times):
        u = active[rng.randrange(len(active))]
        gpu_id = u["assigned_gpu_id"]
        gx, gy = gpu_positions[gpu_id]
        if reuse_fraction > 0 and rng.random() < reuse_fraction:
            reuse_prefix_toks = weighted_choice(rng, REUSE_PREFIX_DIST)
        else:
            reuse_prefix_toks = 0
        input_toks  = max(weighted_choice(rng, INPUT_TOKEN_DIST), reuse_prefix_toks + 64)
        output_toks = weighted_choice(rng, OUTPUT_TOKEN_DIST)
        payload_bytes = PROTOCOL_OVERHEAD_BYTES + input_toks * BYTES_PER_INPUT_TOKEN
        uplink_dist_ns = round(DISTANCE_LATENCY_NS_PER_M * u["distance_to_gpu_m"])
        uplink_ser_ns  = serialization_ns(payload_bytes)
        uplink_ns      = uplink_dist_ns + uplink_ser_ns
        dl_dist_ns     = uplink_dist_ns
        dl_ser_ns      = serialization_ns(FIRST_TOKEN_PAYLOAD_BYTES)
        rows.append({
            "request_id":                    req_id,
            "input_toks":                    input_toks,
            "output_toks":                   output_toks,
            "arrival_time_ns":               send_ns + uplink_ns,
            "request_send_time_ns":          send_ns,
            "reuse_prefix_toks":             reuse_prefix_toks,
            "user_id":                       u["user_id"],
            "user_x_m":                      u["user_x_m"],
            "user_y_m":                      u["user_y_m"],
            "region":                        u["region"],
            "gpu_id":                        gpu_id,
            "nearest_gpu_id":                gpu_id,
            "assigned_instance_id":          gpu_id // pp_size,
            "second_nearest_gpu_id":         u["second_nearest_gpu_id"],
            "second_nearest_distance_m":     u["second_nearest_distance_m"],
            "gpu_x_m":                       gx,
            "gpu_y_m":                       gy,
            "distance_m":                    u["distance_to_gpu_m"],
            "network_throughput_mbps":       NETWORK_THROUGHPUT_MBPS,
            "distance_latency_ns_per_meter": DISTANCE_LATENCY_NS_PER_M,
            "request_payload_bytes":         payload_bytes,
            "first_token_payload_bytes":     FIRST_TOKEN_PAYLOAD_BYTES,
            "uplink_distance_latency_ns":    uplink_dist_ns,
            "uplink_serialization_latency_ns": uplink_ser_ns,
            "uplink_latency_ns":             uplink_ns,
            "downlink_distance_latency_ns":  dl_dist_ns,
            "downlink_serialization_latency_ns": dl_ser_ns,
            "downlink_latency_ns":           dl_dist_ns + dl_ser_ns,
            "communication_latency_ns":      uplink_ns + dl_dist_ns + dl_ser_ns,
            "workload_level":                label,
            "kv_migration_bandwidth_gbps":   10.7,
            "kv_migration_distance_m":       u["second_nearest_distance_m"],
        })
    # restore arrival-time order if Poisson jitter caused inversions
    if any(rows[i]["arrival_time_ns"] >= rows[i+1]["arrival_time_ns"] for i in range(len(rows)-1)):
        rows.sort(key=lambda r: (r["arrival_time_ns"], r["request_id"]))
        for i, r in enumerate(rows):
            r["request_id"] = i
    return rows


def write_workload(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


# ── main ───────────────────────────────────────────────────────────────────

def main() -> None:
    PLACEMENT_DIR.mkdir(parents=True, exist_ok=True)
    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("kagoshima_tokyo_pp1", "kagoshima_tokyo_pp2", "all_tokyo_pp2"):
        (WORKLOAD_DIR / sub).mkdir(exist_ok=True)

    # ── placements ──
    kg_users = build_kagoshima_tokyo_users()
    at_users = build_all_tokyo_users()

    kg_labels = ["tokyo"] * 6 + ["kagoshima"] * 6
    at_labels = ["tokyo"] * 12

    write_gpus_csv(PLACEMENT_DIR / "kagoshima_tokyo_gpus.csv", ALL_GPU_XY, kg_labels)
    write_gpus_csv(PLACEMENT_DIR / "all_tokyo_gpus.csv", ALL_TOKYO_GPU_XY, at_labels)
    write_users_csv(PLACEMENT_DIR / "kagoshima_tokyo_users.csv", kg_users)
    write_users_csv(PLACEMENT_DIR / "all_tokyo_users.csv", at_users)
    print(f"Placements written.")

    rates = request_rates()

    # ── kagoshima-tokyo workloads ──
    for label, rate in rates.items():
        is_heavy = label == "heavy"
        reuse    = REUSE_FRACTION if is_heavy else 0.0
        n_reqs   = NUM_REQUESTS_HEAVY if is_heavy else NUM_REQUESTS
        for pp_size, subdir in ((1, "kagoshima_tokyo_pp1"), (2, "kagoshima_tokyo_pp2")):
            rows = build_workload(label, rate, SEED, ALL_GPU_XY, kg_users, pp_size,
                                  reuse_fraction=reuse, num_requests=n_reqs)
            path = WORKLOAD_DIR / subdir / f"{label}_seed{SEED}.jsonl"
            write_workload(path, rows)
            tokyo_n = sum(1 for r in rows if r["region"] == "tokyo")
            kg_n    = sum(1 for r in rows if r["region"] == "kagoshima")
            reuse_n = sum(1 for r in rows if r["reuse_prefix_toks"] > 0)
            print(f"  {subdir}/{label}_seed{SEED}: {len(rows)} reqs "
                  f"(tokyo={tokyo_n}, kagoshima={kg_n}, reuse={reuse_n})")

    # ── all-tokyo workloads (pp=2 only for the baseline arm) ──
    for label, rate in rates.items():
        is_heavy = label == "heavy"
        reuse    = REUSE_FRACTION if is_heavy else 0.0
        n_reqs   = NUM_REQUESTS_HEAVY if is_heavy else NUM_REQUESTS
        rows = build_workload(label, rate, SEED, ALL_TOKYO_GPU_XY, at_users, pp_size=2,
                              reuse_fraction=reuse, num_requests=n_reqs)
        path = WORKLOAD_DIR / "all_tokyo_pp2" / f"{label}_seed{SEED}.jsonl"
        write_workload(path, rows)
        reuse_n = sum(1 for r in rows if r["reuse_prefix_toks"] > 0)
        print(f"  all_tokyo_pp2/{label}_seed{SEED}: {len(rows)} reqs (reuse={reuse_n})")

    print("Done.")
    print(f"\nRequest rates (rps): {rates}")
    kg_active = [u for u in kg_users if u["is_daily_active"]]
    at_users_active = [u for u in at_users if u["is_daily_active"]]
    kg_city = [u for u in kg_active if u["region"] == "kagoshima"]
    print(f"Kagoshima-Tokyo: {len(kg_active)} active users, "
          f"{sum(1 for u in kg_active if u['region']=='tokyo')} Tokyo-assigned, "
          f"{len(kg_city)} Kagoshima-assigned")
    print(f"All-Tokyo: {len(at_users_active)} active users")
    # propagation latency summary
    kg_latency_ms = [u["distance_to_gpu_m"] * DISTANCE_LATENCY_NS_PER_M / 1e6 for u in kg_city]
    print(f"Kagoshima-assigned propagation (one-way, ms): "
          f"min={min(kg_latency_ms):.2f} mean={sum(kg_latency_ms)/len(kg_latency_ms):.2f} "
          f"max={max(kg_latency_ms):.2f}")


if __name__ == "__main__":
    main()
