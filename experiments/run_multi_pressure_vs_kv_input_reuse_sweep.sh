#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

SOURCE_DIR="experiments/2026-07-14_input_reuse_90s_sweep"
OUTPUT_DIR="experiments/2026-07-21_multi_pressure_vs_kv_input_reuse_sweep"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"

mkdir -p "${OUTPUT_DIR}/results" "${OUTPUT_DIR}/logs"

conditions=(
  input512_reuse00
  input512_reuse025
  input512_reuse05
  input2000_reuse00
  input2000_reuse025
  input2000_reuse05
  input10000_reuse00
  input10000_reuse025
  input10000_reuse05
)

policies=(
  NEAREST_MIGRATE_KV
  NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE
)

run_policy() {
  local condition="$1"
  local policy="$2"
  local workload="${SOURCE_DIR}/workloads/${condition}.jsonl"
  local result_dir="${OUTPUT_DIR}/results/${condition}/${policy}"
  local log_file="${OUTPUT_DIR}/logs/${condition}_${policy}.log"
  mkdir -p "${result_dir}"

  env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true python3 -m serving \
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
    --oneshot-redirect-margin-ns 200000000 \
    --oneshot-max-local-wait-ns 1000000000 \
    --graph-converter in-process \
    --trace-io buffered \
    --output "${result_dir}/requests.csv" \
    --geographic-user-output "${result_dir}/users.csv" \
    --geographic-gpu-output "${result_dir}/gpus.csv" \
    --geographic-metadata-output "${result_dir}/metadata.json" \
    --geographic-users-csv "${USERS_CSV}" \
    --geographic-gpus-csv "${GPUS_CSV}" \
    --run-id "multi-pressure-vs-kv-${condition}-${policy}" \
    --log-level WARNING >"${log_file}" 2>&1
}

tasks=()
for condition in "${conditions[@]}"; do
  workload="${SOURCE_DIR}/workloads/${condition}.jsonl"
  if [[ ! -s "${workload}" ]]; then
    echo "Missing workload: ${workload}" >&2
    exit 1
  fi
  for policy in "${policies[@]}"; do
    requests_csv="${OUTPUT_DIR}/results/${condition}/${policy}/requests.csv"
    if [[ "${SKIP_COMPLETED}" == "1" ]] && [[ -s "${requests_csv}" ]] && \
       [[ "$(wc -l <"${requests_csv}")" -eq 301 ]]; then
      echo "Skipping completed ${condition} ${policy}"
    else
      tasks+=("${condition} ${policy}")
    fi
  done
done

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
    echo "Stopping after a failed simulation; inspect ${OUTPUT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All comparisons completed."
echo "Results: ${OUTPUT_DIR}/results"
