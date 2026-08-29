#!/usr/bin/env python3
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
REPO_ROOT = EXP_ROOT.parents[2]
SOURCE_EXP = REPO_ROOT / "experiments/2026-08-25-storyline/03_geographical_offload_merit"
PP_SIZE = 2


def build_pp2_config() -> None:
    source = json.loads(
        (SOURCE_EXP / "configs/airan_hongo_imbalanced_active20_pp1.json").read_text()
    )
    source_nodes = source["nodes"]
    if len(source_nodes) % PP_SIZE:
        raise ValueError("The PP1 node count must be divisible by PP_SIZE")

    nodes = []
    for start in range(0, len(source_nodes), PP_SIZE):
        group = source_nodes[start:start + PP_SIZE]
        node = json.loads(json.dumps(group[0]))
        instance = node["instances"][0]
        instance["num_npus"] = PP_SIZE
        instance["tp_size"] = 1
        instance["pp_size"] = PP_SIZE
        instance["npu_mem"]["mem_size"] = min(
            member["instances"][0]["npu_mem"]["mem_size"] for member in group
        )
        nodes.append(node)

    source["num_nodes"] = len(nodes)
    source["nodes"] = nodes
    config_dir = EXP_ROOT / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "airan_hongo_imbalanced_active20_pp2.json").write_text(
        json.dumps(source, indent=2) + "\n"
    )


def transform_pp2_workloads() -> None:
    output_dir = EXP_ROOT / "workloads_pp2"
    output_dir.mkdir(parents=True, exist_ok=True)
    for peak in range(2, 6):
        source_path = (
            SOURCE_EXP / "workloads_ai_70_30"
            / f"hongo_peak_{peak}x_repeat600_seed1_ai70_30.jsonl"
        )
        rows = [json.loads(line) for line in source_path.read_text().splitlines()]
        transformed = []
        for row in rows:
            physical_home = int(row["assigned_instance_id"])
            physical_second = int(row["second_nearest_gpu_id"])
            new_row = dict(row)
            new_row.update({
                "physical_assigned_instance_id": physical_home,
                "physical_second_nearest_gpu_id": physical_second,
                "pp_group_id": physical_home // PP_SIZE,
                "pp_stage_id": physical_home % PP_SIZE,
                "assigned_instance_id": physical_home // PP_SIZE,
                "second_nearest_gpu_id": physical_second // PP_SIZE,
            })
            transformed.append(new_row)
        output_path = output_dir / f"hongo_peak_{peak}x_repeat600_seed1_ai70_30_pp2.jsonl"
        output_path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in transformed)
        )


def main() -> None:
    build_pp2_config()
    transform_pp2_workloads()


if __name__ == "__main__":
    main()
