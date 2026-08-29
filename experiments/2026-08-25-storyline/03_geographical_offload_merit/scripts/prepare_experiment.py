#!/usr/bin/env python3
import json
import math
import random
from pathlib import Path

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
REPO_ROOT = EXP_ROOT.parents[2]
SOURCE = REPO_ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test"
BASE_CONFIG = (
    REPO_ROOT
    / "experiments/2026-08-25-storyline/02_merit_of_use_distributed_gpu"
    / "b_ran_aware_economics_20percent/configs/airan_active20_peak_pp1.json"
)
TOTAL_GPU_GIB = 95.577
RRC_PEAK = 900
ACTIVE_TCP_RATIO = 0.20
TOKYO_REQUEST_SHARE = 0.70
WORKLOAD_SEED = 20260826


def main():
    users = pd.read_csv(SOURCE / "placements/hongo_users_8gpu.csv")
    counts = users.groupby("assigned_gpu_id").size().sort_index()
    if counts.sum() != 27_000 or list(counts.index) != list(range(8)):
        raise ValueError("Unexpected Hongo user placement")

    config = json.loads(BASE_CONFIG.read_text())
    rows = []
    for gpu_id, user_count in counts.items():
        rrc_connected = RRC_PEAK * int(user_count) / int(counts.sum())
        active_ue = math.ceil(rrc_connected * ACTIVE_TCP_RATIO)
        ran_vram_fraction = 0.40 + 0.016 * active_ue
        ai_vram_gib = TOTAL_GPU_GIB * (1.0 - ran_vram_fraction)
        config["nodes"][int(gpu_id)]["instances"][0]["npu_mem"]["mem_size"] = round(
            ai_vram_gib, 6
        )
        rows.append(
            {
                "gpu_id": int(gpu_id),
                "registered_users": int(user_count),
                "peak_rrc_connected_ue": rrc_connected,
                "active_tcp_ue": active_ue,
                "ran_vram_fraction": ran_vram_fraction,
                "ai_vram_gib": ai_vram_gib,
            }
        )

    config_dir = EXP_ROOT / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "airan_hongo_imbalanced_active20_pp1.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )
    pd.DataFrame(rows).to_csv(config_dir / "gpu_ran_vram_assignment.csv", index=False)

    workload_dir = EXP_ROOT / "workloads_ai_70_30"
    workload_dir.mkdir(parents=True, exist_ok=True)
    gpu_positions = pd.read_csv(SOURCE / "placements/hongo_gh200_gpus.csv").set_index(
        "gpu_id"
    )
    for peak in range(2, 11):
        source_path = (
            SOURCE
            / "workloads_cloud_aligned"
            / f"hongo_peak_{peak}x_repeat600_seed1.jsonl"
        )
        requests = [json.loads(line) for line in source_path.read_text().splitlines()]
        session_sizes = {}
        for row in requests:
            session_id = int(row["session_id"])
            session_sizes[session_id] = session_sizes.get(session_id, 0) + 1
        sessions = sorted(session_sizes)
        shuffled = sessions.copy()
        random.Random(WORKLOAD_SEED).shuffle(shuffled)
        tokyo_request_target = round(len(requests) * TOKYO_REQUEST_SHARE)
        tokyo_sessions = set()
        tokyo_request_count = 0
        for session_id in shuffled:
            session_size = session_sizes[session_id]
            if tokyo_request_count + session_size <= tokyo_request_target:
                tokyo_sessions.add(session_id)
                tokyo_request_count += session_size
            if tokyo_request_count == tokyo_request_target:
                break
        if tokyo_request_count != tokyo_request_target:
            raise ValueError("Could not construct an exact 70/30 session-preserving split")
        site_counters = {"tokyo": 0, "kagoshima": 0}
        for row in requests:
            site = "tokyo" if int(row["session_id"]) in tokyo_sessions else "kagoshima"
            offset = 0 if site == "tokyo" else 4
            gpu_id = offset + site_counters[site] % 4
            site_counters[site] += 1
            second_gpu_id = offset + (gpu_id - offset + 1) % 4
            gpu = gpu_positions.loc[gpu_id]
            second_gpu = gpu_positions.loc[second_gpu_id]
            row.update({
                "gpu_id": gpu_id,
                "nearest_gpu_id": gpu_id,
                "assigned_instance_id": gpu_id,
                "gpu_x_m": float(gpu["gpu_x_m"]),
                "gpu_y_m": float(gpu["gpu_y_m"]),
                "second_nearest_gpu_id": second_gpu_id,
                "second_nearest_distance_m": math.hypot(
                    float(row["user_x_m"]) - float(second_gpu["gpu_x_m"]),
                    float(row["user_y_m"]) - float(second_gpu["gpu_y_m"]),
                ),
                "ai_request_site": site,
            })
            row["distance_m"] = math.hypot(
                float(row["user_x_m"]) - float(gpu["gpu_x_m"]),
                float(row["user_y_m"]) - float(gpu["gpu_y_m"]),
            )
        output_path = workload_dir / f"hongo_peak_{peak}x_repeat600_seed1_ai70_30.jsonl"
        output_path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in requests)
        )


if __name__ == "__main__":
    main()
