#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test/configs/gh200_hongo_8gpu_cloud_aligned.json"
OUTPUT = ROOT / "experiments/2026-08-24-simulate-both-local-and-cloudlike/configs"


def write_config(name: str, link_bw: float, link_latency: int) -> None:
    with SOURCE.open() as file:
        config = json.load(file)
    config["link_bw"] = link_bw
    config["link_latency"] = link_latency
    with (OUTPUT / name).open("w") as file:
        json.dump(config, file, indent=2)
        file.write("\n")


write_config("gh200_no_pp_distributed_apn.json", 1.3375, 300500)
write_config("gh200_no_pp_distributed_normal_wan.json", 0.125, 5000000)
