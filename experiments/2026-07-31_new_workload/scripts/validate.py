#!/usr/bin/env python3
"""Validate generated urban workload artifacts without running the simulator."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    metadata = json.loads(
        (EXPERIMENT_DIR / "configs/experiment.json").read_text(encoding="utf-8")
    )
    cluster_paths = (
        EXPERIMENT_DIR / "configs/gh200_class_20gpu_proxy.json",
        EXPERIMENT_DIR / "configs/gh200_20gpu.json",
    )
    clusters = [
        json.loads(path.read_text(encoding="utf-8")) for path in cluster_paths
    ]
    if metadata["geography"]["area_km2"] != 11.0:
        raise ValueError("Area must be 11 km2")
    if metadata["geography"]["population"] != 200_000:
        raise ValueError("Population must be 200,000")
    for cluster in clusters:
        if cluster["num_nodes"] != 20 or len(cluster["nodes"]) != 20:
            raise ValueError("Cluster must contain 20 nodes")
        instances = [
            instance
            for node in cluster["nodes"]
            for instance in node["instances"]
        ]
        if len(instances) != 20 or sum(item["num_npus"] for item in instances) != 20:
            raise ValueError("Cluster must contain 20 one-GPU instances")
        if any(item["npu_mem"]["mem_size"] != 96 for item in instances):
            raise ValueError("Every GPU must have 96 GB memory")
        if any(item["npu_mem"]["mem_bw"] != 4_000 for item in instances):
            raise ValueError("Every GPU must have 4,000 GB/s memory bandwidth")
    proxy_hardware = clusters[0]["nodes"][0]["instances"][0]["hardware"]
    native_hardware = clusters[1]["nodes"][0]["instances"][0]["hardware"]
    if proxy_hardware != "RTXPRO6000" or native_hardware != "GH200":
        raise ValueError("Unexpected proxy/native hardware labels")

    with (EXPERIMENT_DIR / "placements/users.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        users = list(csv.DictReader(handle))
    if len(users) != 200_000:
        raise ValueError("User placement must contain 200,000 rows")
    active_users = {int(row["user_id"]) for row in users if row["is_daily_active"] == "True"}
    if len(active_users) != 20_000:
        raise ValueError("Daily-active set must contain 20,000 users")

    with (EXPERIMENT_DIR / "placements/gpus.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        gpus = list(csv.DictReader(handle))
    if len(gpus) != 20:
        raise ValueError("GPU placement must contain 20 rows")

    expected_rates = metadata["traffic_model"]["rates_rps"]
    workload_paths = sorted((EXPERIMENT_DIR / "workloads").glob("*.jsonl"))
    if len(workload_paths) != 9:
        raise ValueError("Expected 9 workload files")
    for path in workload_paths:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if len(rows) != 3_000:
            raise ValueError(f"{path}: expected 3,000 requests")
        if any(
            left["arrival_time_ns"] >= right["arrival_time_ns"]
            for left, right in zip(rows, rows[1:])
        ):
            raise ValueError(f"{path}: arrivals are not strictly increasing")
        if any(row["user_id"] not in active_users for row in rows):
            raise ValueError(f"{path}: request from a non-active user")
        if any(not 0 <= row["gpu_id"] < 20 for row in rows):
            raise ValueError(f"{path}: invalid GPU id")
        if any(row["assigned_instance_id"] != row["gpu_id"] for row in rows):
            raise ValueError(f"{path}: nearest instance assignment mismatch")
        if any(row["reuse_prefix_toks"] != 0 for row in rows):
            raise ValueError(f"{path}: baseline prefix reuse must be zero")
        level = rows[0]["workload_level"]
        duration_s = rows[-1]["request_send_time_ns"] / 1e9
        realized_rate = len(rows) / duration_s
        if not math.isclose(realized_rate, expected_rates[level], rel_tol=1e-9):
            raise ValueError(f"{path}: incorrect realized request rate")
    print("Validated 200,000 users, 20 GPUs, and 9 workload files")


if __name__ == "__main__":
    main()
