#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

EXPERIMENT_DIR="experiments/2026-07-14_input_reuse_90s_sweep"
SOURCE_WORKLOAD="workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"
MAX_PARALLEL=3

mkdir -p "${EXPERIMENT_DIR}/workloads"
mkdir -p "${EXPERIMENT_DIR}/results"
mkdir -p "${EXPERIMENT_DIR}/logs"

generate_workload() {
  local input_tokens="$1"
  local reuse_rate="$2"
  local condition="$3"
  local workload="${EXPERIMENT_DIR}/workloads/${condition}.jsonl"

  python - \
    "${SOURCE_WORKLOAD}" \
    "${workload}" \
    "${input_tokens}" \
    "${reuse_rate}" <<'PY'
import json
import math
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
input_tokens = int(sys.argv[3])
reuse_rate = float(sys.argv[4])
block_size = 16

reuse_prefix_toks = math.floor(input_tokens * reuse_rate)
reuse_prefix_toks = reuse_prefix_toks // block_size * block_size

output_path.parent.mkdir(parents=True, exist_ok=True)

with source_path.open(encoding="utf-8") as source:
    with output_path.open("w", encoding="utf-8") as output:
        for line in source:
            if not line.strip():
                continue

            row = json.loads(line)
            input_tok_ids = row["input_tok_ids"]
            if len(input_tok_ids) < input_tokens:
                raise ValueError(
                    f"Request {row.get('request_id')} only has "
                    f"{len(input_tok_ids)} input token IDs"
                )

            row["input_toks"] = input_tokens
            row["input_tok_ids"] = input_tok_ids[:input_tokens]
            row["reuse_prefix_toks"] = reuse_prefix_toks
            row["request_payload_bytes"] = 500 + input_tokens * 4
            output.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )

print(
    f"Prepared {output_path}: input_tokens={input_tokens}, "
    f"reuse_prefix_toks={reuse_prefix_toks}"
)
PY
}

run_policy() {
  local condition="$1"
  local policy="$2"
  local workload="${EXPERIMENT_DIR}/workloads/${condition}.jsonl"
  local output_dir="${EXPERIMENT_DIR}/results/${condition}/${policy}"
  local log_file="${EXPERIMENT_DIR}/logs/${condition}_${policy}.log"
  local time_file="${EXPERIMENT_DIR}/logs/${condition}_${policy}.time"
  local run_id="input-reuse-90s-${condition}-${policy}"

  mkdir -p "${output_dir}"

  {
    time python -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --dataset "${workload}" \
      --num-reqs 300 \
      --request-routing-policy "${policy}" \
      --dtype bfloat16 \
      --kv-cache-dtype auto \
      --max-num-seqs 128 \
      --max-num-batched-tokens 2048 \
      --enable-chunked-prefill \
      --enable-prefix-caching \
      --gpu-backbone-bandwidth-gbps 10.7 \
      --apn-fixed-propagation-ns 300500 \
      --kv-staging-bandwidth-gbytes-per-s 33.8 \
      --kv-staging-latency-ns 102.9 \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --geographic-users-csv "${USERS_CSV}" \
      --geographic-gpus-csv "${GPUS_CSV}" \
      --run-id "${run_id}" \
      --log-level WARNING
  } >"${log_file}" 2>"${time_file}"
}

# Recreate all workload files deterministically. Existing result CSVs are not
# touched until their corresponding simulation is explicitly launched below.
generate_workload 512 0.0 input512_reuse00
generate_workload 512 0.25 input512_reuse025
generate_workload 512 0.5 input512_reuse05
generate_workload 2000 0.0 input2000_reuse00
generate_workload 2000 0.25 input2000_reuse025
generate_workload 2000 0.5 input2000_reuse05

tasks=(
  "input512_reuse00 NEAREST_KV"
  "input512_reuse025 NEAREST_KV"
  "input512_reuse025 NEAREST_MIGRATE"
  "input512_reuse025 NEAREST_MIGRATE_KV"
  "input512_reuse05 NEAREST_KV"
  "input512_reuse05 NEAREST_MIGRATE"
  "input512_reuse05 NEAREST_MIGRATE_KV"
  "input2000_reuse00 NEAREST_KV"
  "input2000_reuse00 NEAREST_MIGRATE"
  "input2000_reuse00 NEAREST_MIGRATE_KV"
  "input2000_reuse025 NEAREST_KV"
  "input2000_reuse025 NEAREST_MIGRATE"
  "input2000_reuse025 NEAREST_MIGRATE_KV"
  "input2000_reuse05 NEAREST_KV"
  "input2000_reuse05 NEAREST_MIGRATE"
  "input2000_reuse05 NEAREST_MIGRATE_KV"
)

echo "Prepared ${#tasks[@]} simulations with max parallelism ${MAX_PARALLEL}."

for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()

  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    read -r condition policy <<<"${tasks[start + offset]}"
    echo "Starting ${condition} ${policy}"
    run_policy "${condition}" "${policy}" &
    pids+=("$!")
    labels+=("${condition} ${policy}")
  done

  failed=0
  for index in "${!pids[@]}"; do
    if wait "${pids[index]}"; then
      echo "Completed ${labels[index]}"
    else
      echo "Failed ${labels[index]}" >&2
      failed=1
    fi
  done

  if ((failed)); then
    echo "Stopping after a failed simulation; inspect ${EXPERIMENT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All remaining simulations completed."
echo "Results: ${EXPERIMENT_DIR}/results"
echo "Logs: ${EXPERIMENT_DIR}/logs"
