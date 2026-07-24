"""regional-ratio output -> fixed 3-4-3 GPU grid workload (10cell_apn spec).

Consumes the output of `python -m workloads.generators regional-ratio`
(which already assigns `user_id` = `gpu_id*users_per_gpu + local_user` and
`gpu_id`/`assigned_instance_id` = region_id, alphabetical) and layers on the
fields `serving/core/router.py`'s NEAREST_KV/NEAREST_REJECT/NEAREST_MIGRATE/
NEAREST_MIGRATE_KV policies need: fixed 3-4-3 staggered-grid GPU coordinates,
Voronoi-stratified per-user placement (each GPU's cell gets exactly
`--users-per-gpu` users, uniform-random within the cell), the actual
nearest/second-nearest GPU computed from those coordinates, and
`reuse_prefix_toks` derived from `--kv-reuse-ratio`.

`arrival_time_ns` from the input is preserved as-is (already GPU-arrival
time from the regional-ratio generator) -- no separate UE uplink is modeled
here; per the 10cell_apn spec, all communication cost (UE resend, GPU
forward, KV migration) is charged by the router at simulation time via
`--apn-fixed-propagation-ns`/`--gpu-backbone-bandwidth-gbps`/
`--kv-staging-*`, not baked into the workload.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
from pathlib import Path

# Spec section 4.2: fixed 3-4-3 staggered grid over a 10,000 x 10,000 m area.
# Index = gpu_id = region_id (alphabetical Arizona..Virginia, matching
# regional_ratio.py's `sorted(regions)` assignment).
GPU_COORDS_M = [
    (1666.667, 2113.249),
    (5000.000, 2113.249),
    (8333.333, 2113.249),
    (0.000, 5000.000),
    (3333.333, 5000.000),
    (6666.667, 5000.000),
    (10000.000, 5000.000),
    (1666.667, 7886.751),
    (5000.000, 7886.751),
    (8333.333, 7886.751),
]

def register_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", required=True,
                    help="Output of `regional-ratio` (has user_id/gpu_id/arrival_time_ns).")
    p.add_argument("--output", required=True, help="Output extended workload JSONL path.")
    p.add_argument("--users-output", dest="users_output", required=True, help="Static per-user placement CSV path.")
    p.add_argument("--gpus-output", dest="gpus_output", required=True, help="Static per-GPU placement CSV path.")
    p.add_argument("--metadata-output", dest="metadata_output", required=True, help="Generation metadata JSON path.")

    p.add_argument("--area-width-m", dest="area_width_m", type=float, default=10000.0)
    p.add_argument("--area-height-m", dest="area_height_m", type=float, default=10000.0)
    p.add_argument("--users-per-gpu", dest="users_per_gpu", type=int, default=2)

    p.add_argument("--network-throughput-mbps", dest="network_throughput_mbps", type=float, default=10700.0,
                    help="UE-facing serialization bandwidth for redirect/resend legs; default matches the "
                    "10.7 Gbit/s APN link (spec section 6).")
    p.add_argument("--distance-latency-ns-per-meter", dest="distance_latency_ns_per_meter", type=float, default=5.0,
                    help="Written for schema completeness only -- inert at simulation time once "
                    "--apn-fixed-propagation-ns is passed to `python -m serving`.")
    p.add_argument("--protocol-overhead-bytes", dest="protocol_overhead_bytes", type=float, default=500.0)
    p.add_argument("--bytes-per-input-token", dest="bytes_per_input_token", type=float, default=4.0)
    p.add_argument("--first-token-payload-bytes", dest="first_token_payload_bytes", type=float, default=100.0)

    p.add_argument("--kv-reuse-ratio", dest="kv_reuse_ratio", type=float, default=0.5,
                    help="reuse_prefix_toks = floor(floor(input_toks * ratio) / block_size) * block_size "
                    "(spec section 7.2).")
    p.add_argument("--block-size", dest="block_size", type=int, default=16)

    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-placement-attempts", dest="max_placement_attempts", type=int, default=100_000)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _nearest_two(xy: tuple[float, float], gpus: list[tuple[float, float]]) -> tuple[int, float, int, float]:
    """Return (nearest_id, nearest_dist, second_nearest_id, second_nearest_dist).

    Ties broken by GPU id ascending, matching geographic.py's `_two_nearest_gpus`.
    """
    ux, uy = xy
    ranked = sorted(
        (math.sqrt((ux - gx) ** 2 + (uy - gy) ** 2), gpu_id)
        for gpu_id, (gx, gy) in enumerate(gpus)
    )
    (dist0, id0), (dist1, id1) = ranked[0], ranked[1]
    return id0, dist0, id1, dist1


def _place_user_in_cell(target_gpu_id: int, gpus: list[tuple[float, float]],
                         width: float, height: float, rng: random.Random,
                         max_attempts: int) -> tuple[float, float]:
    """Rejection-sample (x, y) uniform in the area until its nearest GPU is target_gpu_id."""
    for _ in range(max_attempts):
        x = rng.uniform(0.0, width)
        y = rng.uniform(0.0, height)
        nearest_id, _, _, _ = _nearest_two((x, y), gpus)
        if nearest_id == target_gpu_id:
            return x, y
    raise RuntimeError(
        f"Failed to place a user inside GPU {target_gpu_id}'s Voronoi cell after "
        f"{max_attempts} attempts; check --area-width-m/--area-height-m against GPU_COORDS_M."
    )


def run(args: argparse.Namespace) -> int:
    if len(GPU_COORDS_M) < 2:
        raise ValueError("Need at least 2 GPUs to compute a second-nearest GPU.")

    rng = random.Random(args.seed)

    in_path = Path(args.input)
    out_path = Path(args.output)
    users_out_path = Path(args.users_output)
    gpus_out_path = Path(args.gpus_output)
    metadata_out_path = Path(args.metadata_output)
    for p in (out_path, users_out_path, gpus_out_path, metadata_out_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    num_gpus = len(GPU_COORDS_M)
    num_users = num_gpus * args.users_per_gpu

    # Canonical user order matches regional_ratio.py: user_id = gpu_id*users_per_gpu + local_user.
    users_xy: list[tuple[float, float] | None] = [None] * num_users
    for gpu_id in range(num_gpus):
        for local_user in range(args.users_per_gpu):
            user_id = gpu_id * args.users_per_gpu + local_user
            users_xy[user_id] = _place_user_in_cell(
                gpu_id, GPU_COORDS_M, args.area_width_m, args.area_height_m,
                rng, args.max_placement_attempts,
            )

    # Nearest/second-nearest are fixed once placement is fixed -- compute once,
    # and verify the stratification actually landed each user in its assigned
    # GPU's Voronoi cell (don't just assume the rejection sampling above worked).
    nearest: list[tuple[int, float, int, float]] = []
    for user_id, xy in enumerate(users_xy):
        home_gpu_id = user_id // args.users_per_gpu
        nearest_id, nearest_dist, second_id, second_dist = _nearest_two(xy, GPU_COORDS_M)
        if nearest_id != home_gpu_id:
            raise RuntimeError(
                f"user {user_id}: nearest GPU {nearest_id} != assigned home GPU {home_gpu_id} "
                f"(placement bug in _place_user_in_cell)."
            )
        nearest.append((nearest_id, nearest_dist, second_id, second_dist))

    written = 0
    with in_path.open(encoding="utf-8") as f_in, out_path.open("w", encoding="utf-8") as f_out:
        for line in f_in:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "sub_requests" in row:
                raise RuntimeError(
                    f"Agentic session rows (sub_requests) are out of scope: {in_path} line {written + 1}"
                )

            user_id = int(row["user_id"])
            if not (0 <= user_id < num_users):
                raise ValueError(f"source row user_id {user_id} out of range [0, {num_users - 1}]")

            ux, uy = users_xy[user_id]
            gpu_id, distance_m, second_nearest_gpu_id, second_nearest_distance_m = nearest[user_id]
            gx, gy = GPU_COORDS_M[gpu_id]

            input_toks = int(row["input_toks"])
            request_payload_bytes = args.protocol_overhead_bytes + input_toks * args.bytes_per_input_token

            reuse_prefix_toks = math.floor(input_toks * args.kv_reuse_ratio)
            if args.block_size > 1:
                reuse_prefix_toks = reuse_prefix_toks // args.block_size * args.block_size

            out_row = dict(row)  # keeps input_tok_ids/output_tok_ids/region/arrival_time_ns, etc.
            out_row.update({
                "request_id": written,
                "user_id": user_id,
                "user_x_m": ux,
                "user_y_m": uy,

                "assigned_instance_id": gpu_id,
                "gpu_id": gpu_id,
                "gpu_x_m": gx,
                "gpu_y_m": gy,
                "distance_m": distance_m,
                "second_nearest_gpu_id": second_nearest_gpu_id,
                "second_nearest_distance_m": second_nearest_distance_m,

                "network_throughput_mbps": args.network_throughput_mbps,
                "distance_latency_ns_per_meter": args.distance_latency_ns_per_meter,

                "request_payload_bytes": request_payload_bytes,
                "first_token_payload_bytes": args.first_token_payload_bytes,

                "request_send_time_ns": int(row["arrival_time_ns"]),

                "reuse_prefix_toks": reuse_prefix_toks,
            })
            f_out.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            written += 1

    # --- static users CSV (all num_users users, including senders of zero requests) ---
    with users_out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "user_x_m", "user_y_m",
                          "request_weight", "request_probability",
                          "assigned_gpu_id", "distance_to_gpu_m",
                          "second_nearest_gpu_id", "second_nearest_distance_m"])
        request_weight = 1.0
        request_probability = 1.0 / num_users
        for user_id in range(num_users):
            ux, uy = users_xy[user_id]
            gpu_id, dist, second_gpu_id, second_dist = nearest[user_id]
            writer.writerow([
                user_id, ux, uy, request_weight, request_probability,
                gpu_id, dist, second_gpu_id, second_dist,
            ])

    # --- static GPUs CSV ---
    with gpus_out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["gpu_id", "instance_id", "gpu_x_m", "gpu_y_m"])
        for gpu_id, (gx, gy) in enumerate(GPU_COORDS_M):
            writer.writerow([gpu_id, gpu_id, gx, gy])

    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, text=True
        ).strip()
    except Exception:
        git_hash = None

    metadata = {
        "area_width_m": args.area_width_m,
        "area_height_m": args.area_height_m,
        "num_users": num_users,
        "num_gpus": num_gpus,
        "users_per_gpu": args.users_per_gpu,
        "gpu_placement": "fixed_3_4_3_staggered_grid",
        "user_placement": "voronoi_stratified_uniform_random",
        "seed": args.seed,
        "network_throughput_mbps": args.network_throughput_mbps,
        "distance_latency_ns_per_meter": args.distance_latency_ns_per_meter,
        "protocol_overhead_bytes": args.protocol_overhead_bytes,
        "bytes_per_input_token": args.bytes_per_input_token,
        "first_token_payload_bytes": args.first_token_payload_bytes,
        "kv_reuse_ratio": args.kv_reuse_ratio,
        "block_size": args.block_size,
        "input_workload": str(in_path),
        "output_workload": str(out_path),
        "users_output": str(users_out_path),
        "gpus_output": str(gpus_out_path),
        "requests_written": written,
        "git_commit_hash": git_hash,
        "communication_model": (
            "no UE uplink modeled here; redirect/migration/KV-migration costs are charged "
            "by serving/core/router.py at simulation time via --apn-fixed-propagation-ns / "
            "--gpu-backbone-bandwidth-gbps / --kv-staging-bandwidth-gbytes-per-s / "
            "--kv-staging-latency-ns"
        ),
    }
    with metadata_out_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"Wrote {written:,} requests -> {out_path}")
    print(f"Wrote {num_users:,} users -> {users_out_path}")
    print(f"Wrote {num_gpus:,} GPUs -> {gpus_out_path}")
    print(f"Wrote metadata -> {metadata_out_path}")
    return 0
