import csv
import itertools
import json
import os
import subprocess
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.dirname(__file__)

base = {
    "num_nodes": 1,
    "link_bw": 16,
    "link_latency": 20000,
    "nodes": [{
        "num_instances": 1,
        "cpu_mem": {"mem_size": 512, "mem_bw": 256, "mem_latency": 0},
        "instances": [{
            "model_name": "meta-llama/Llama-3.1-8B",
            "hardware": "RTXPRO6000",
            "npu_mem": {"mem_size": 24, "mem_bw": 1597, "mem_latency": 0},
            "num_npus": 1,
            "tp_size": 1,
            "pd_type": None,
        }],
    }],
}

rows = []
for weights, kv_loc, kv_evict_loc in itertools.product(("npu", "cpu"), repeat=3):
    name = f"w-{weights}_kv-{kv_loc}_evict-{kv_evict_loc}"
    config = json.loads(json.dumps(base))
    config["nodes"][0]["instances"][0]["placement"] = {
        "default": {
            "weights": weights,
            "kv_loc": kv_loc,
            "kv_evict_loc": kv_evict_loc,
        }
    }
    config_path = os.path.join(OUT, f"{name}.json")
    output_path = os.path.join(OUT, f"{name}.csv")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    command = [
        sys.executable, "-m", "serving",
        "--cluster-config", os.path.relpath(config_path, ROOT),
        "--dataset", "workloads/hetero_prompt_mix_32.jsonl",
        "--num-reqs", "8",
        "--max-num-seqs", "8",
        "--max-num-batched-tokens", "2048",
        "--no-enable-prefix-caching",
        "--output", os.path.relpath(output_path, ROOT),
        "--run-id", name,
        "--log-level", "WARNING",
    ]
    log_path = os.path.join(OUT, f"{name}.log")
    with open(log_path, "w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f"{name} failed; see {log_path}")

    with open(output_path, newline="", encoding="utf-8") as f:
        requests = list(csv.DictReader(f))
    rows.append({
        "weights": weights,
        "kv_loc": kv_loc,
        "kv_evict_loc": kv_evict_loc,
        "requests": len(requests),
        "mean_ttft_ms": sum(float(r["TTFT"]) for r in requests) / len(requests) / 1e6,
        "mean_tpot_ms": sum(float(r["TPOT"]) for r in requests) / len(requests) / 1e6,
        "mean_e2e_ms": sum(float(r["latency"]) for r in requests) / len(requests) / 1e6,
        "makespan_ms": max(float(r["end_time"]) for r in requests) / 1e6,
    })

summary_path = os.path.join(OUT, "summary.csv")
with open(summary_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(summary_path)
