#!/usr/bin/env python3
"""Run the Hongo 1x-10x four-arm comparison with peak RAN VRAM reserved."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXP_ROOT.parents[1]
SOURCE_EXP = REPO_ROOT / "experiments/2026-08-01_hongo_workload"
TRACE_PATH = EXP_ROOT / "hongo_ran_vram_trace_12ru.csv"
GENERATED_CONFIG_DIR = EXP_ROOT / "generated_configs"
RESULTS_DIR = EXP_ROOT / "results_peak_ran"
LOGS_DIR = EXP_ROOT / "logs_peak_ran"

LEVELS = range(1, 11)
ARMS = (
    ("1_no_redirect", "NEAREST_KV", "pp1", ()),
    ("2_redirect_no_kv", "NEAREST_MIGRATE", "pp1", ()),
    (
        "3_redirect_kv_pp1",
        "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE",
        "pp1",
        (),
    ),
    (
        "4_redirect_kv_pp2",
        "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE",
        "pp2",
        ("--pp-size", "2"),
    ),
)


def load_name(level: int) -> str:
    return "busy_hour" if level == 1 else f"peak_{level}x"


def peak_ran_vram_per_gpu() -> float:
    with TRACE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"RAN VRAM trace is empty: {TRACE_PATH}")
    gpu_counts = {int(row["gpu_count"]) for row in rows}
    gpu_sizes = {float(row["gpu_vram_gb"]) for row in rows}
    if gpu_counts != {12} or gpu_sizes != {24.0}:
        raise ValueError(
            "Expected a 12-GPU, 24-GB/GPU RAN trace; "
            f"found gpu_count={sorted(gpu_counts)}, gpu_vram_gb={sorted(gpu_sizes)}"
        )
    return max(float(row["ran_vram_per_gpu_gb"]) for row in rows)


def write_cluster_config(kind: str, inference_vram_gb: float) -> Path:
    suffix = "_pp2" if kind == "pp2" else ""
    source = SOURCE_EXP / "configs" / f"rtx4090_hongo{suffix}.json"
    config = json.loads(source.read_text(encoding="utf-8"))
    for node in config["nodes"]:
        for instance in node["instances"]:
            instance["npu_mem"]["mem_size"] = inference_vram_gb
    config["ran_vram_reservation"] = {
        "source": str(TRACE_PATH.relative_to(REPO_ROOT)),
        "policy": "peak_per_gpu",
        "reserved_per_gpu_gb": 24.0 - inference_vram_gb,
        "inference_per_gpu_gb": inference_vram_gb,
    }
    GENERATED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    output = GENERATED_CONFIG_DIR / f"rtx4090_hongo_peak_ran{suffix}.json"
    output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return output


def run_case(level: int, arm: tuple, configs: dict[str, Path], num_reqs: int) -> tuple[str, int]:
    arm_name, policy, kind, extra_flags = arm
    load = load_name(level)
    run_name = f"{level}x_seed1_{arm_name}"
    workload_suffix = "_pp2" if kind == "pp2" else ""
    workload = SOURCE_EXP / "workloads" / f"hongo_{load}_seed1{workload_suffix}.jsonl"
    outdir = RESULTS_DIR / run_name
    outdir.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "serving",
        "--cluster-config",
        str(configs[kind].relative_to(REPO_ROOT)),
        "--dataset",
        str(workload.relative_to(REPO_ROOT)),
        "--request-routing-policy",
        policy,
        "--num-reqs",
        str(num_reqs),
        "--max-num-seqs",
        "128",
        "--max-num-batched-tokens",
        "2048",
        "--gpu-backbone-bandwidth-gbps",
        "10.7",
        "--apn-fixed-propagation-ns",
        "300500",
        "--kv-staging-bandwidth-gbytes-per-s",
        "33.8",
        "--kv-staging-latency-ns",
        "102.9",
        "--inputs-root",
        f"/tmp/astra_runs/peak_ran_{run_name}",
        "--output",
        str((outdir / "requests.csv").relative_to(REPO_ROOT)),
        "--geographic-user-output",
        str((outdir / "users.csv").relative_to(REPO_ROOT)),
        "--geographic-gpu-output",
        str((outdir / "gpus.csv").relative_to(REPO_ROOT)),
        "--geographic-metadata-output",
        str((outdir / "metadata.json").relative_to(REPO_ROOT)),
        "--geographic-users-csv",
        str((SOURCE_EXP / "placements/hongo_users.csv").relative_to(REPO_ROOT)),
        "--geographic-gpus-csv",
        str((SOURCE_EXP / "placements/hongo_gpus.csv").relative_to(REPO_ROOT)),
        "--run-id",
        f"hongo-peak-ran-{run_name}",
        "--log-level",
        "WARNING",
        *extra_flags,
    ]
    with (LOGS_DIR / f"{run_name}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT)
    return run_name, result.returncode


def case_complete(level: int, arm: tuple, num_reqs: int) -> bool:
    run_name = f"{level}x_seed1_{arm[0]}"
    outdir = RESULTS_DIR / run_name
    required = ("requests.csv", "users.csv", "gpus.csv", "metadata.json")
    if not all((outdir / name).is_file() and (outdir / name).stat().st_size > 0 for name in required):
        return False
    try:
        with (outdir / "requests.csv").open(newline="", encoding="utf-8") as handle:
            return sum(1 for _ in csv.DictReader(handle)) == num_reqs
    except (OSError, csv.Error):
        return False


def main() -> int:
    max_parallel = int(os.environ.get("MAX_PARALLEL", "3"))
    num_reqs = int(os.environ.get("NUM_REQS", "300"))
    reserved_gb = peak_ran_vram_per_gpu()
    inference_gb = 24.0 - reserved_gb
    configs = {
        kind: write_cluster_config(kind, inference_gb)
        for kind in ("pp1", "pp2")
    }
    manifest = {
        "ran_vram_trace": str(TRACE_PATH.relative_to(REPO_ROOT)),
        "ran_reservation_policy": "peak_per_gpu",
        "ran_vram_reserved_per_gpu_gb": reserved_gb,
        "inference_vram_per_gpu_gb": inference_gb,
        "levels": list(LEVELS),
        "num_requests_per_case": num_reqs,
        "arms": [arm[0] for arm in ARMS],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    failures = []
    futures = []
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        for level in LEVELS:
            for arm in ARMS:
                if case_complete(level, arm, num_reqs):
                    print(f"[skip] {level}x_seed1_{arm[0]}", flush=True)
                    continue
                futures.append(executor.submit(run_case, level, arm, configs, num_reqs))
        for future in as_completed(futures):
            try:
                run_name, returncode = future.result()
            except Exception as exc:
                failures.append(f"runner exception: {exc}")
                print(f"[FAIL runner] {exc}", flush=True)
                continue
            status = "done" if returncode == 0 else f"FAIL rc={returncode}"
            print(f"[{status}] {run_name}", flush=True)
            if returncode:
                failures.append(run_name)
    if failures:
        print(f"Failed cases: {', '.join(sorted(failures))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
