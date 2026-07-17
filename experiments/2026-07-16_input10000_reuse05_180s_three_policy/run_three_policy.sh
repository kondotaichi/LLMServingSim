#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

EXPERIMENT_DIR="experiments/2026-07-16_input10000_reuse05_180s_three_policy"
WORKLOAD="${EXPERIMENT_DIR}/workloads/input10000_reuse05_180s.jsonl"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"
MAX_PARALLEL="${MAX_PARALLEL:-3}"

run_policy() {
  local policy="$1"
  local output_dir="${EXPERIMENT_DIR}/results/${policy}"
  local log_file="${EXPERIMENT_DIR}/logs/${policy}.log"
  local time_file="${EXPERIMENT_DIR}/logs/${policy}.time"
  local run_id="input10000-reuse05-180s-${policy}"

  mkdir -p "${output_dir}"
  {
    time python -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --dataset "${WORKLOAD}" \
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

if [[ -n "${ONLY_POLICY:-}" ]]; then
  policies=("${ONLY_POLICY}")
else
  policies=(NEAREST_KV NEAREST_MIGRATE NEAREST_MIGRATE_KV)
fi

for ((start = 0; start < ${#policies[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#policies[@]}; offset++)); do
    policy="${policies[start + offset]}"
    echo "Starting ${policy}"
    run_policy "${policy}" &
    pids+=("$!")
    labels+=("${policy}")
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
    exit 1
  fi
done

echo "All 180-second simulations completed."
