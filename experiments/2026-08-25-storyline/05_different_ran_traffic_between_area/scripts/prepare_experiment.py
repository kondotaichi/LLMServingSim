#!/usr/bin/env python3
import json
import math
from pathlib import Path

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
REPO_ROOT = EXP_ROOT.parents[2]
SOURCE_EXP = REPO_ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test"
BASE_CONFIG = (
    REPO_ROOT
    / "experiments/2026-08-25-storyline/03_geographical_offload_merit"
    / "configs/airan_hongo_imbalanced_active20_pp1.json"
)

TOTAL_GPU_GIB = 95.577
RRC_PEAK = 900
TOKYO_ACTIVE_TCP_RATIO = 0.23
KAGOSHIMA_ACTIVE_TCP_RATIO = 0.17
TOKYO_GPU_IDS = set(range(4))
PP_SIZE = 2


def main():
    users = pd.read_csv(SOURCE_EXP / "placements/hongo_users_8gpu.csv")
    counts = users.groupby("assigned_gpu_id").size().sort_index()
    if counts.sum() != 27_000 or list(counts.index) != list(range(8)):
        raise ValueError("Unexpected Hongo user placement")

    config = json.loads(BASE_CONFIG.read_text())
    rows = []
    for gpu_id, user_count in counts.items():
        site = "tokyo" if gpu_id in TOKYO_GPU_IDS else "kagoshima"
        active_ratio = (
            TOKYO_ACTIVE_TCP_RATIO
            if site == "tokyo"
            else KAGOSHIMA_ACTIVE_TCP_RATIO
        )
        rrc_connected = RRC_PEAK * int(user_count) / int(counts.sum())
        active_ue = math.ceil(rrc_connected * active_ratio)
        ran_vram_fraction = 0.40 + 0.016 * active_ue
        ai_vram_gib = TOTAL_GPU_GIB * (1.0 - ran_vram_fraction)
        config["nodes"][int(gpu_id)]["instances"][0]["npu_mem"]["mem_size"] = round(
            ai_vram_gib, 6
        )
        rows.append(
            {
                "gpu_id": int(gpu_id),
                "site": site,
                "registered_users": int(user_count),
                "peak_rrc_connected_ue": rrc_connected,
                "active_tcp_ratio": active_ratio,
                "active_tcp_ue": active_ue,
                "ran_vram_fraction": ran_vram_fraction,
                "ai_vram_gib": ai_vram_gib,
            }
        )

    config_dir = EXP_ROOT / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "airan_tokyo23_kagoshima17_pp1.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )
    assignment = pd.DataFrame(rows)
    assignment.to_csv(config_dir / "gpu_ran_vram_assignment.csv", index=False)

    pp2_config = json.loads(json.dumps(config))
    pp2_nodes = []
    for start in range(0, len(config["nodes"]), PP_SIZE):
        group = config["nodes"][start:start + PP_SIZE]
        node = json.loads(json.dumps(group[0]))
        instance = node["instances"][0]
        instance["num_npus"] = PP_SIZE
        instance["tp_size"] = 1
        instance["pp_size"] = PP_SIZE
        instance["npu_mem"]["mem_size"] = min(
            member["instances"][0]["npu_mem"]["mem_size"] for member in group
        )
        pp2_nodes.append(node)
    pp2_config["num_nodes"] = len(pp2_nodes)
    pp2_config["nodes"] = pp2_nodes
    (config_dir / "airan_tokyo23_kagoshima17_pp2.json").write_text(
        json.dumps(pp2_config, indent=2) + "\n"
    )

    source_workload = (
        REPO_ROOT
        / "experiments/2026-08-25-storyline/03_geographical_offload_merit"
        / "workloads_ai_70_30/hongo_peak_5x_repeat600_seed1_ai70_30.jsonl"
    )
    workload_dir = EXP_ROOT / "workloads_pp2"
    workload_dir.mkdir(parents=True, exist_ok=True)
    transformed = []
    for line in source_workload.read_text().splitlines():
        row = json.loads(line)
        physical_home = int(row["assigned_instance_id"])
        physical_second = int(row["second_nearest_gpu_id"])
        row.update({
            "physical_assigned_instance_id": physical_home,
            "physical_second_nearest_gpu_id": physical_second,
            "pp_group_id": physical_home // PP_SIZE,
            "pp_stage_id": physical_home % PP_SIZE,
            "assigned_instance_id": physical_home // PP_SIZE,
            "second_nearest_gpu_id": physical_second // PP_SIZE,
        })
        transformed.append(row)
    (workload_dir / "hongo_peak_5x_repeat600_seed1_ai70_30_pp2.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in transformed)
    )

    print(assignment.to_string(index=False))
    print()
    print(assignment.groupby("site").agg(
        active_tcp_ue=("active_tcp_ue", "sum"),
        mean_ran_vram_fraction=("ran_vram_fraction", "mean"),
        mean_ai_vram_gib=("ai_vram_gib", "mean"),
    ).to_string())


if __name__ == "__main__":
    main()
