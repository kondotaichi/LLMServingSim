#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

EXPERIMENT_DIR="experiments/2026-07-16_ttft_component_regression"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"
MAX_PARALLEL="${MAX_PARALLEL:-2}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
TASK_LIMIT="${TASK_LIMIT:-0}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PHASE1_CONDITIONS="${PHASE1_CONDITIONS:-}"

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 3)); then
  echo "MAX_PARALLEL must be 1, 2, or 3 for this experiment." >&2
  exit 2
fi

"${PYTHON_BIN}" "${EXPERIMENT_DIR}/scripts/prepare_phase1_workloads.py"
mkdir -p "${EXPERIMENT_DIR}/results/phase1"
mkdir -p "${EXPERIMENT_DIR}/logs/phase1"
mkdir -p "${EXPERIMENT_DIR}/status/phase1"

run_policy() {
  local condition="$1"
  local policy="$2"
  local workload="${EXPERIMENT_DIR}/workloads/phase1/${condition}.jsonl"
  local output_dir="${EXPERIMENT_DIR}/results/phase1/${condition}/${policy}"
  local log_file="${EXPERIMENT_DIR}/logs/phase1/${condition}_${policy}.log"
  local time_file="${EXPERIMENT_DIR}/logs/phase1/${condition}_${policy}.time"
  local status_file="${EXPERIMENT_DIR}/status/phase1/${condition}_${policy}.status"
  local run_id="ttft-regression-phase1-${condition}-${policy}"

  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"
  if {
    time env PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
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
  } >"${log_file}" 2>"${time_file}"; then
    if [[ -s "${output_dir}/requests.csv" ]] && [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]]; then
      echo "COMPLETED" >"${status_file}"
      return 0
    fi
    echo "INVALID_OUTPUT" >"${status_file}"
    return 1
  else
    echo "FAILED" >"${status_file}"
    return 1
  fi
}

tasks=()
while IFS=, read -r condition input_tokens request_rate reuse_ratio seed requests last_arrival workload; do
  [[ "${condition}" == "condition" ]] && continue
  if [[ -n "${PHASE1_CONDITIONS}" ]] && [[ ",${PHASE1_CONDITIONS}," != *",${condition},"* ]]; then
    continue
  fi
  for policy in NEAREST_KV NEAREST_MIGRATE NEAREST_MIGRATE_KV; do
    output_csv="${EXPERIMENT_DIR}/results/phase1/${condition}/${policy}/requests.csv"
    if [[ "${SKIP_COMPLETED}" == "1" ]] && [[ -s "${output_csv}" ]] && [[ "$(wc -l <"${output_csv}")" -eq 301 ]]; then
      echo "Skipping completed ${condition} ${policy}"
      continue
    fi
    tasks+=("${condition} ${policy}")
  done
done <"${EXPERIMENT_DIR}/configs/phase1_manifest.csv"

if ((TASK_LIMIT > 0 && ${#tasks[@]} > TASK_LIMIT)); then
  tasks=("${tasks[@]:0:TASK_LIMIT}")
fi

echo "Prepared ${#tasks[@]} simulations with max parallelism ${MAX_PARALLEL}."
total_failed=0
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
    total_failed=1
    if [[ "${STOP_ON_FAILURE}" == "1" ]]; then
      echo "Stopping after a failed simulation. Rerun with SKIP_COMPLETED=1 after inspection." >&2
      exit 1
    fi
    echo "Continuing after a failed simulation; inspect status/phase1 and logs/phase1." >&2
  fi
done

echo "All Phase 1 simulations completed."
if ((total_failed)); then
  echo "One or more simulations failed; rerun with SKIP_COMPLETED=1." >&2
  exit 1
fi
