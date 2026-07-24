#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKLOAD="workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 5)); then
  echo "MAX_PARALLEL must be between 1 and 5." >&2
  exit 2
fi

mkdir -p "${SCRIPT_DIR}/results" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

policies=(
  NEAREST_KV
  NEAREST_MIGRATE
  NEAREST_MIGRATE_KV
  NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE
  NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE
)

is_complete() {
  local output_dir="$1"
  local policy="$2"
  [[ -s "${output_dir}/requests.csv" ]] && \
    [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]] && \
    [[ -s "${output_dir}/gpus.csv" ]] && \
    head -n 1 "${output_dir}/gpus.csv" | grep -q 'utilization_pct' && \
    [[ -s "${output_dir}/gpu_utilization_timeseries.csv" ]] && \
    { [[ "${policy}" != "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE" ]] || \
      [[ -s "${output_dir}/routing_candidates.csv" ]]; }
}

run_policy() {
  local policy="$1"
  local output_dir="${SCRIPT_DIR}/results/${policy}"
  local log_file="${SCRIPT_DIR}/logs/${policy}.log"
  local status_file="${SCRIPT_DIR}/status/${policy}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
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
      --oneshot-redirect-margin-ns 200000000 \
      --oneshot-max-local-wait-ns 1000000000 \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --gpu-utilization-timeseries-output "${output_dir}/gpu_utilization_timeseries.csv" \
      --gpu-utilization-window-ns 1000000000 \
      --routing-candidate-output "${output_dir}/routing_candidates.csv" \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --geographic-users-csv "${USERS_CSV}" \
      --geographic-gpus-csv "${GPUS_CSV}" \
      --run-id "prompt6000-90s-util-${policy}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if is_complete "${output_dir}" "${policy}"; then
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
for policy in "${policies[@]}"; do
  if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${SCRIPT_DIR}/results/${policy}" "${policy}"; then
    echo "Skipping completed ${policy}"
  else
    tasks+=("${policy}")
  fi
done

echo "Prepared ${#tasks[@]} simulations with max parallelism ${MAX_PARALLEL}."
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    policy="${tasks[start + offset]}"
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
    echo "Stopping after a failed simulation; inspect ${SCRIPT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All five-policy simulations completed."
echo "Run the analyzer to generate utilization figures."
