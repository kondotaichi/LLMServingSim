#!/usr/bin/env python3
"""Generate a cluster config for a given PP degree over the fixed 24-GPU
Hongo pool: NUM_PHYSICAL_GPUS / pp_size logical instances, one node per
instance. Each instance is written with pp_size=1 / num_npus=1; the actual
PP degree is applied at run time via `python -m serving --pp-size N`
(serving/core/config_builder.py::_apply_pp_size_override), matching the
precedent in experiments/2026-08-01_hongo_workload/configs/rtx4090_hongo_pp2.json.
"""

import argparse
import json
from pathlib import Path

NUM_PHYSICAL_GPUS = 24
MODEL_NAME = "meta-llama/Llama-3.1-8B"
HARDWARE = "RTX4090"


def cluster_config(pp_size: int) -> dict:
    if NUM_PHYSICAL_GPUS % pp_size != 0:
        raise ValueError(f"pp-size {pp_size} must divide NUM_PHYSICAL_GPUS ({NUM_PHYSICAL_GPUS})")
    num_instances = NUM_PHYSICAL_GPUS // pp_size
    nodes = []
    for _ in range(num_instances):
        nodes.append({
            "num_instances": 1,
            "cpu_mem": {"mem_size": 128, "mem_bw": 33.8, "mem_latency": 102.9},
            "instances": [{
                "model_name": MODEL_NAME,
                "hardware": HARDWARE,
                "npu_mem": {"mem_size": 24, "mem_bw": 1_008, "mem_latency": 0},
                "pd_type": None,
                "num_npus": 1,
                "tp_size": 1,
                "pp_size": 1,
            }],
        })
    return {"num_nodes": num_instances, "link_bw": 16, "link_latency": 20_000, "nodes": nodes}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pp-size", type=int, required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    Path(args.output).write_text(json.dumps(cluster_config(args.pp_size), indent=2) + "\n")
    print(f"Wrote {args.output} ({NUM_PHYSICAL_GPUS // args.pp_size} instances at pp_size={args.pp_size})")


if __name__ == "__main__":
    main()
