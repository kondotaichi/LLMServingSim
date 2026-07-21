#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENT_REL="experiments/2026-07-21_mixed_workload_model_value"
cd "${REPO_ROOT}"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 3)); then
  echo "MAX_PARALLEL must be 1, 2, or 3." >&2
  exit 2
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/prepare_workloads.py"
mkdir -p "${SCRIPT_DIR}/results" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

policies=(
  NEAREST_MIGRATE_KV
  NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE
  NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE
)

run_policy() {
  local condition="$1"
  local policy="$2"
  local workload="${EXPERIMENT_REL}/workloads/${condition}.jsonl"
  local output_dir="${SCRIPT_DIR}/results/${condition}/${policy}"
  local log_file="${SCRIPT_DIR}/logs/${condition}_${policy}.log"
  local status_file="${SCRIPT_DIR}/status/${condition}_${policy}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
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
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --geographic-users-csv "${USERS_CSV}" \
      --geographic-gpus-csv "${GPUS_CSV}" \
      --run-id "mixed-model-value-${condition}-${policy}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if [[ -s "${output_dir}/requests.csv" ]] && \
       [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]]; then
      echo "COMPLETED" >"${status_file}"
      return 0
    fi
    echo "INVALID_OUTPUT" >"${status_file}"
    return 1
  fi
  echo "FAILED" >"${status_file}"
  return 1
}

tasks=()
while IFS=, read -r condition _; do
  [[ "${condition}" == "condition" ]] && continue
  for policy in "${policies[@]}"; do
    output_csv="${SCRIPT_DIR}/results/${condition}/${policy}/requests.csv"
    if [[ "${SKIP_COMPLETED}" == "1" ]] && [[ -s "${output_csv}" ]] && \
       [[ "$(wc -l <"${output_csv}")" -eq 301 ]]; then
      echo "Skipping completed ${condition} ${policy}"
    else
      tasks+=("${condition} ${policy}")
    fi
  done
done <"${SCRIPT_DIR}/configs/workload_manifest.csv"

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
    echo "Stopping after a failed simulation; inspect ${SCRIPT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All mixed-workload comparisons completed."
echo "Results: ${SCRIPT_DIR}/results"
