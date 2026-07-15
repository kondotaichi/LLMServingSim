#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

EXPERIMENT_DIR="experiments/2026-07-14_input_reuse_90s_sweep"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-0}"

python3 "${EXPERIMENT_DIR}/scripts/prepare_input10000_workloads.py"

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

tasks=(
  "input10000_reuse00 NEAREST_KV"
  "input10000_reuse00 NEAREST_MIGRATE"
  "input10000_reuse00 NEAREST_MIGRATE_KV"
  "input10000_reuse025 NEAREST_KV"
  "input10000_reuse025 NEAREST_MIGRATE"
  "input10000_reuse025 NEAREST_MIGRATE_KV"
  "input10000_reuse05 NEAREST_KV"
  "input10000_reuse05 NEAREST_MIGRATE"
  "input10000_reuse05 NEAREST_MIGRATE_KV"
)

if [[ "${SKIP_COMPLETED}" == "1" ]]; then
  pending_tasks=()
  for task in "${tasks[@]}"; do
    read -r condition policy <<<"${task}"
    requests_csv="${EXPERIMENT_DIR}/results/${condition}/${policy}/requests.csv"
    if [[ -s "${requests_csv}" ]] && [[ "$(wc -l <"${requests_csv}")" -eq 301 ]]; then
      echo "Skipping completed ${condition} ${policy}"
    else
      pending_tasks+=("${task}")
    fi
  done
  tasks=("${pending_tasks[@]}")
fi

echo "Prepared ${#tasks[@]} simulations with max parallelism ${MAX_PARALLEL}."

if ((${#tasks[@]} == 0)); then
  echo "No remaining simulations."
  exit 0
fi

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

echo "All input-10000 simulations completed."
