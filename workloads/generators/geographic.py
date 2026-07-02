"""Existing workload JSONL -> geographically-distributed UE/GPU workload.

Phase 1 of the geographic distributed-inference simulation. Takes an existing
flat LLMServingSim JSONL (e.g. a ShareGPT trace) and, without touching its
arrival-time distribution, assigns each request a sending user (from 3000
uniformly-placed UEs) and that user's nearest GPU (from a 20-GPU grid), then
computes a simple analytical uplink/downlink communication delay (fixed
throughput + distance-proportional latency, no ns-3, no contention/queueing/
jitter/packet-loss).

Output rows keep every original field (``input_toks``, ``output_toks``,
``input_tok_ids``, ``output_tok_ids``, prefix-caching / session fields) and
add the geographic + communication fields described in the Phase 1 spec.
``arrival_time_ns`` is overwritten to mean "GPU arrival time" (existing
Router/Scheduler semantics are unchanged); the original send time is
preserved separately as ``request_send_time_ns``.

Agentic session rows (``sub_requests``) are out of scope for Phase 1 and
raise a clear error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def register_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", required=True, help="Source flat JSONL (arrival_time_ns is preserved, never regenerated).")
    p.add_argument("--user-frequency-file", dest="user_frequency_file", default=None,
                    help="CSV with columns user_id,request_weight. Required unless "
                    "--allow-uniform-user-fallback is passed or every source row already has user_id.")
    p.add_argument("--output", required=True, help="Output extended workload JSONL path.")
    p.add_argument("--users-output", dest="users_output", required=True, help="Static per-user placement CSV path.")
    p.add_argument("--gpus-output", dest="gpus_output", required=True, help="Static per-GPU placement CSV path.")
    p.add_argument("--metadata-output", dest="metadata_output", required=True, help="Generation metadata JSON path.")

    p.add_argument("--area-width-m", dest="area_width_m", type=float, default=1000.0)
    p.add_argument("--area-height-m", dest="area_height_m", type=float, default=1000.0)
    p.add_argument("--num-users", dest="num_users", type=int, default=3000)
    p.add_argument("--gpu-rows", dest="gpu_rows", type=int, default=4)
    p.add_argument("--gpu-cols", dest="gpu_cols", type=int, default=5)

    p.add_argument("--network-throughput-mbps", dest="network_throughput_mbps", type=float, default=100.0)
    p.add_argument("--distance-latency-ns-per-meter", dest="distance_latency_ns_per_meter", type=float, default=5.0)
    p.add_argument("--protocol-overhead-bytes", dest="protocol_overhead_bytes", type=float, default=500.0)
    p.add_argument("--bytes-per-input-token", dest="bytes_per_input_token", type=float, default=4.0)
    p.add_argument("--first-token-payload-bytes", dest="first_token_payload_bytes", type=float, default=100.0)

    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--allow-uniform-user-fallback", dest="allow_uniform_user_fallback",
                    action="store_true", default=False,
                    help="Explicit opt-in: if no --user-frequency-file is given and a source row "
                    "lacks user_id, sample its sender uniformly at random instead of erroring.")


# ---------------------------------------------------------------------------
# Placement generation
# ---------------------------------------------------------------------------

def _generate_users(num_users: int, width: float, height: float, rng: random.Random) -> list[tuple[float, float]]:
    return [(rng.uniform(0.0, width), rng.uniform(0.0, height)) for _ in range(num_users)]


def _generate_gpus(rows: int, cols: int, width: float, height: float) -> list[tuple[float, float]]:
    """Grid placement, id = r*cols + c, bottom row (r=0) to top, left (c=0) to right."""
    gpus = []
    for r in range(rows):
        for c in range(cols):
            x = (c + 0.5) * width / cols
            y = (r + 0.5) * height / rows
            gpus.append((x, y))
    return gpus


def _nearest_gpu(user_xy: tuple[float, float], gpus: list[tuple[float, float]]) -> tuple[int, float]:
    ux, uy = user_xy
    best_id = 0
    best_dist = math.inf
    for gpu_id, (gx, gy) in enumerate(gpus):
        d = math.sqrt((ux - gx) ** 2 + (uy - gy) ** 2)
        if d < best_dist:
            best_dist = d
            best_id = gpu_id
    return best_id, best_dist


# ---------------------------------------------------------------------------
# User frequency distribution
# ---------------------------------------------------------------------------

def _load_user_weights(path: Path, num_users: int) -> list[float]:
    weights = [0.0] * num_users
    seen = set()
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"user_id", "request_weight"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path}: expected columns {sorted(required)}, got {reader.fieldnames}")
        for lineno, row in enumerate(reader, start=2):
            try:
                uid = int(row["user_id"])
                w = float(row["request_weight"])
            except (TypeError, ValueError) as e:
                raise ValueError(f"{path}:{lineno}: invalid row {row!r}: {e}") from e
            if not (0 <= uid < num_users):
                raise ValueError(f"{path}:{lineno}: user_id {uid} out of range [0, {num_users - 1}]")
            if uid in seen:
                raise ValueError(f"{path}:{lineno}: duplicate user_id {uid}")
            if w < 0:
                raise ValueError(f"{path}:{lineno}: request_weight {w} is negative")
            seen.add(uid)
            weights[uid] = w
    if sum(weights) <= 0:
        raise ValueError(f"{path}: sum of request_weight must be > 0")
    return weights


# ---------------------------------------------------------------------------
# Communication model
# ---------------------------------------------------------------------------

def _serialization_ns(payload_bytes: float, mbps: float) -> float:
    # T_ns = 8 * bytes * 1e9 / (mbps * 1e6) == 8000 * bytes / mbps
    return 8000.0 * payload_bytes / mbps


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)

    in_path = Path(args.input)
    out_path = Path(args.output)
    users_out_path = Path(args.users_output)
    gpus_out_path = Path(args.gpus_output)
    metadata_out_path = Path(args.metadata_output)
    for p in (out_path, users_out_path, gpus_out_path, metadata_out_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    num_users = args.num_users
    users_xy = _generate_users(num_users, args.area_width_m, args.area_height_m, rng)
    gpus_xy = _generate_gpus(args.gpu_rows, args.gpu_cols, args.area_width_m, args.area_height_m)
    num_gpus = len(gpus_xy)

    # Nearest GPU is fixed once user/GPU positions are fixed -- compute once.
    nearest = [_nearest_gpu(xy, gpus_xy) for xy in users_xy]  # (gpu_id, dist_m) per user

    used_uniform_fallback = False
    weights = None
    if args.user_frequency_file is not None:
        weights = _load_user_weights(Path(args.user_frequency_file), num_users)
    request_ids = list(range(num_users))
    weight_sum = sum(weights) if weights is not None else 0.0

    def select_user(row: dict) -> int:
        nonlocal used_uniform_fallback
        if "user_id" in row:
            uid = int(row["user_id"])
            if not (0 <= uid < num_users):
                raise ValueError(f"source row user_id {uid} out of range [0, {num_users - 1}]")
            return uid
        if weights is not None:
            return rng.choices(request_ids, weights=weights, k=1)[0]
        if args.allow_uniform_user_fallback:
            used_uniform_fallback = True
            return rng.randrange(num_users)
        raise RuntimeError(
            "No --user-frequency-file given, source row has no user_id, and "
            "--allow-uniform-user-fallback was not passed. Refusing to silently "
            "fall back to a uniform user distribution (Phase 1 spec 10.5)."
        )

    request_count = [0] * num_users

    written = 0
    with in_path.open(encoding="utf-8") as f_in, out_path.open("w", encoding="utf-8") as f_out:
        for line in f_in:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "sub_requests" in row:
                raise RuntimeError(
                    f"Agentic session rows (sub_requests) are out of scope for the "
                    f"Phase 1 geographic generator: {in_path} line {written + 1}"
                )

            user_id = select_user(row)
            request_count[user_id] += 1
            ux, uy = users_xy[user_id]
            gpu_id, distance_m = nearest[user_id]
            gx, gy = gpus_xy[gpu_id]

            input_toks = int(row["input_toks"])
            request_payload_bytes = args.protocol_overhead_bytes + input_toks * args.bytes_per_input_token
            first_token_payload_bytes = args.first_token_payload_bytes

            uplink_distance_latency_ns = round(args.distance_latency_ns_per_meter * distance_m)
            uplink_serialization_latency_ns = round(_serialization_ns(request_payload_bytes, args.network_throughput_mbps))
            uplink_latency_ns = uplink_distance_latency_ns + uplink_serialization_latency_ns

            downlink_distance_latency_ns = round(args.distance_latency_ns_per_meter * distance_m)
            downlink_serialization_latency_ns = round(_serialization_ns(first_token_payload_bytes, args.network_throughput_mbps))
            downlink_latency_ns = downlink_distance_latency_ns + downlink_serialization_latency_ns

            communication_latency_ns = uplink_latency_ns + downlink_latency_ns

            request_send_time_ns = int(row["arrival_time_ns"])
            gpu_arrival_time_ns = request_send_time_ns + uplink_latency_ns

            out_row = dict(row)  # shallow copy -- keeps input_tok_ids/output_tok_ids/prefix/session fields
            out_row.update({
                "request_id": written,
                "user_id": user_id,
                "user_x_m": ux,
                "user_y_m": uy,

                "request_send_time_ns": request_send_time_ns,

                "assigned_instance_id": gpu_id,
                "gpu_id": gpu_id,
                "gpu_x_m": gx,
                "gpu_y_m": gy,
                "distance_m": distance_m,

                "network_throughput_mbps": args.network_throughput_mbps,
                "distance_latency_ns_per_meter": args.distance_latency_ns_per_meter,

                "request_payload_bytes": request_payload_bytes,
                "first_token_payload_bytes": first_token_payload_bytes,

                "uplink_distance_latency_ns": uplink_distance_latency_ns,
                "uplink_serialization_latency_ns": uplink_serialization_latency_ns,
                "uplink_latency_ns": uplink_latency_ns,

                "downlink_distance_latency_ns": downlink_distance_latency_ns,
                "downlink_serialization_latency_ns": downlink_serialization_latency_ns,
                "downlink_latency_ns": downlink_latency_ns,

                "communication_latency_ns": communication_latency_ns,

                "gpu_arrival_time_ns": gpu_arrival_time_ns,
                "arrival_time_ns": gpu_arrival_time_ns,  # overwritten: existing Router reads this as GPU arrival
            })
            f_out.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            written += 1

    # --- static users CSV (all num_users rows, including senders of zero requests) ---
    with users_out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "user_x_m", "user_y_m", "request_weight", "request_probability",
                          "assigned_gpu_id", "distance_to_gpu_m"])
        for uid in range(num_users):
            ux, uy = users_xy[uid]
            gpu_id, dist = nearest[uid]
            w = weights[uid] if weights is not None else ""
            prob = (weights[uid] / weight_sum) if (weights is not None and weight_sum > 0) else ""
            writer.writerow([uid, ux, uy, w, prob, gpu_id, dist])

    # --- static GPUs CSV ---
    with gpus_out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["gpu_id", "instance_id", "gpu_x_m", "gpu_y_m"])
        for gpu_id, (gx, gy) in enumerate(gpus_xy):
            writer.writerow([gpu_id, gpu_id, gx, gy])

    # --- metadata JSON ---
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
        "gpu_rows": args.gpu_rows,
        "gpu_cols": args.gpu_cols,
        "gpu_placement": "grid_equal_spacing",
        "user_placement": "uniform_random",
        "user_frequency_file": args.user_frequency_file,
        "user_selection_method": (
            "uniform_fallback" if used_uniform_fallback else
            "weighted_sampling" if weights is not None else
            "source_user_id"
        ),
        "allow_uniform_user_fallback": args.allow_uniform_user_fallback,
        "uniform_fallback_used": used_uniform_fallback,
        "seed": args.seed,
        "network_throughput_mbps": args.network_throughput_mbps,
        "distance_latency_ns_per_meter": args.distance_latency_ns_per_meter,
        "protocol_overhead_bytes": args.protocol_overhead_bytes,
        "bytes_per_input_token": args.bytes_per_input_token,
        "first_token_payload_bytes": args.first_token_payload_bytes,
        "input_workload": str(in_path),
        "output_workload": str(out_path),
        "users_output": str(users_out_path),
        "gpus_output": str(gpus_out_path),
        "requests_written": written,
        "git_commit_hash": git_hash,
        "network_model": "fixed_throughput_distance_proportional",
        "network_contention": False,
        "network_queueing": False,
        "network_jitter": False,
        "packet_loss": False,
        "user_mobility": False,
        "routing_policy": "nearest",
        "ttft_definition": (
            "simulator_ttft = first_token_ready - gpu_arrival; "
            "e2e_ttft = uplink_latency_ns + simulator_ttft + downlink_latency_ns"
        ),
        "prefill_service_definition": (
            "sum of wall-clock batch durations (ASTRA-Sim-reported finish - start) over every "
            "batch this request participated in while its TTFT was not yet stamped; a batch's "
            "duration is attributed to every request in it, not divided exclusively"
        ),
        "decode_definition": (
            "decode_after_ttft_ns = decode_queueing_ns + decode_active_ns, measured from "
            "first-token-ready to request completion; never added into e2e_ttft_ns"
        ),
        "bottleneck_definition": (
            "ttft_bottleneck = argmax(communication_latency_ns, queueing_before_ttft_ns, "
            "prefill_service_ns), ties broken queueing > prefill > communication; "
            "total_latency_bottleneck additionally compares decode_after_ttft_ns, ties broken "
            "queueing > prefill > decode > communication"
        ),
    }
    with metadata_out_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"Wrote {written:,} requests -> {out_path}")
    print(f"Wrote {num_users:,} users -> {users_out_path}")
    print(f"Wrote {num_gpus:,} GPUs -> {gpus_out_path}")
    print(f"Wrote metadata -> {metadata_out_path}")
    return 0
