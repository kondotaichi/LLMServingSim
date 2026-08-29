#!/usr/bin/env python3
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPT_DIR.parent
REPO_ROOT = EXP_ROOT.parents[3]
SOURCE_ROOT = REPO_ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test"
PRIOR_ROOT = REPO_ROOT / "experiments/2026-08-24-simulate-both-local-and-cloudlike"
CONFIG_ROOT = EXP_ROOT / "configs"

PHYSICAL_VRAM_GB = 95.577
PEAK_RAN_VRAM_FRACTION = 0.592
AI_RAN_VRAM_GB = PHYSICAL_VRAM_GB * (1.0 - PEAK_RAN_VRAM_FRACTION)
ACTIVE20_RAN_VRAM_FRACTION = 0.768
ACTIVE20_AI_VRAM_GB = PHYSICAL_VRAM_GB * (1.0 - ACTIVE20_RAN_VRAM_FRACTION)


def load(path):
    with path.open() as stream:
        return json.load(stream)


def set_memory(config, memory_gb):
    for node in config["nodes"]:
        for instance in node["instances"]:
            instance["npu_mem"]["mem_size"] = round(memory_gb, 6)


def write(name, config):
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    with (CONFIG_ROOT / name).open("w") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")


def main():
    cloud_pp1 = load(SOURCE_ROOT / "configs/gh200_8gpu_clustered_cloud.json")
    airan_pp1 = load(SOURCE_ROOT / "configs/gh200_hongo_8gpu_cloud_aligned.json")
    cloud_pp2 = load(PRIOR_ROOT / "configs/gh200_pp2_clustered_cloud.json")
    airan_pp2 = load(PRIOR_ROOT / "configs/gh200_pp2_clustered_cloud.json")

    set_memory(airan_pp1, AI_RAN_VRAM_GB)
    set_memory(airan_pp2, AI_RAN_VRAM_GB)

    active20_pp1 = load(SOURCE_ROOT / "configs/gh200_hongo_8gpu_cloud_aligned.json")
    active20_pp2 = load(PRIOR_ROOT / "configs/gh200_pp2_clustered_cloud.json")
    set_memory(active20_pp1, ACTIVE20_AI_VRAM_GB)
    set_memory(active20_pp2, ACTIVE20_AI_VRAM_GB)

    write("cloud_pp1.json", cloud_pp1)
    write("airan_peak_pp1.json", airan_pp1)
    write("cloud_pp2.json", cloud_pp2)
    write("airan_peak_pp2.json", airan_pp2)
    write("airan_active20_peak_pp1.json", active20_pp1)
    write("airan_active20_peak_pp2.json", active20_pp2)

    print(f"AI-RAN VRAM per GPU: {AI_RAN_VRAM_GB:.6f} GB")
    print(
        "Active-20% AI-RAN VRAM per GPU: "
        f"{ACTIVE20_AI_VRAM_GB:.6f} GB"
    )


if __name__ == "__main__":
    main()
