#!/usr/bin/env python3
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXP_ROOT = REPO_ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test"
INPUT_ROOT = EXP_ROOT / "workloads_cloud_aligned"
OUTPUT_ROOT = EXP_ROOT / "workloads_clustered_cloud"
GPU_X_M = 374.1657383467427
GPU_Y_M = 374.1657383467427


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for level in range(1, 11):
        filename = f"hongo_peak_{level}x_repeat600_seed1.jsonl"
        output_rows = []
        with (INPUT_ROOT / filename).open() as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if len(rows) != 600:
            raise ValueError(f"{filename}: expected 600 rows")

        for row in rows:
            assigned = int(row["assigned_instance_id"])
            distance_m = math.hypot(
                float(row["user_x_m"]) - GPU_X_M,
                float(row["user_y_m"]) - GPU_Y_M,
            )
            distance_ns = round(
                distance_m * float(row["distance_latency_ns_per_meter"])
            )
            uplink_serialization_ns = round(
                8000.0
                * float(row["request_payload_bytes"])
                / float(row["network_throughput_mbps"])
            )
            downlink_serialization_ns = round(
                8000.0
                * float(row["first_token_payload_bytes"])
                / float(row["network_throughput_mbps"])
            )
            uplink_ns = distance_ns + uplink_serialization_ns
            downlink_ns = distance_ns + downlink_serialization_ns

            row.update(
                {
                    "gpu_id": assigned,
                    "nearest_gpu_id": assigned,
                    "second_nearest_gpu_id": (assigned + 1) % 8,
                    "gpu_x_m": GPU_X_M,
                    "gpu_y_m": GPU_Y_M,
                    "distance_m": distance_m,
                    "second_nearest_distance_m": distance_m,
                    "uplink_distance_latency_ns": distance_ns,
                    "uplink_serialization_latency_ns": uplink_serialization_ns,
                    "uplink_latency_ns": uplink_ns,
                    "downlink_distance_latency_ns": distance_ns,
                    "downlink_serialization_latency_ns": downlink_serialization_ns,
                    "downlink_latency_ns": downlink_ns,
                    "communication_latency_ns": uplink_ns + downlink_ns,
                    "arrival_time_ns": int(row["request_send_time_ns"]) + uplink_ns,
                }
            )
            output_rows.append(row)

        with (OUTPUT_ROOT / filename).open("w") as handle:
            for row in output_rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(OUTPUT_ROOT / filename)


if __name__ == "__main__":
    main()
