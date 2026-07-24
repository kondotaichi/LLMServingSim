#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENT_REL="experiments/2026-07-21_mixed_workload_model_value"
cd "${REPO_ROOT}"

# Ablation of the three axes behind Multi no-model's win over KV migrate /
# Multi learned (see kondoFolder/diary/2026-07-22-seminor-report.md):
#   1. candidate count  -- Dynamic (1 candidate) vs Multi (N candidates)
#   2. model usage       -- no-model / pressure+model-gate / full formula
#   3. reservation        -- Multi with/without atomic target reservation
# Scoped to the two rate=3.33 seeds that actually produce enough redirects
# to carry a signal (rate=2.5 seeds have 2-6 redirects and show no
# separation between policies).

MAX_PARALLEL="${MAX_PARALLEL:-8}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USERS_CSV="workloads/generated/cell_apn/users.csv"
GPUS_CSV="workloads/generated/cell_apn/gpus.csv"

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 8)); then
  echo "MAX_PARALLEL must be between 1 and 8." >&2
  exit 2
fi

mkdir -p "${SCRIPT_DIR}/results" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

conditions=(
  mixed_rate3p33_seed1
  mixed_rate3p33_seed2
)

# output_name : request-routing-policy : extra CLI flags (may be empty)
runs=(
  "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE:NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE:"
  "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE:NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE:"
  "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE_NO_RESERVATION:NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE:--no-enable-oneshot-target-reservation"
  "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_NO_RESERVATION:NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE:--no-enable-oneshot-target-reservation"
)

is_complete() {
  local output_dir="$1"
  local requests_csv="${output_dir}/requests.csv"
  local gpus_csv="${output_dir}/gpus.csv"
  [[ -s "${requests_csv}" ]] && \
    [[ "$(wc -l <"${requests_csv}")" -eq 301 ]] && \
    [[ -s "${gpus_csv}" ]] && \
    head -n 1 "${gpus_csv}" | grep -q 'utilization_pct'
}

run_one() {
  local condition="$1"
  local output_name="$2"
  local routing_policy="$3"
  local extra_flags="$4"
  local workload="${EXPERIMENT_REL}/workloads/${condition}.jsonl"
  local output_dir="${SCRIPT_DIR}/results/${condition}/${output_name}"
  local log_file="${SCRIPT_DIR}/logs/${condition}_${output_name}.log"
  local status_file="${SCRIPT_DIR}/status/${condition}_${output_name}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --dataset "${workload}" \
      --num-reqs 300 \
      --request-routing-policy "${routing_policy}" \
      ${extra_flags} \
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
      --run-id "ablation-${condition}-${output_name}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if is_complete "${output_dir}"; then
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
for condition in "${conditions[@]}"; do
  for run in "${runs[@]}"; do
    IFS=: read -r output_name routing_policy extra_flags <<<"${run}"
    if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${SCRIPT_DIR}/results/${condition}/${output_name}"; then
      echo "Skipping completed ${condition} ${output_name}"
    else
      tasks+=("${condition}:${output_name}:${routing_policy}:${extra_flags}")
    fi
  done
done

echo "Prepared ${#tasks[@]} simulations with max parallelism ${MAX_PARALLEL}."
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    IFS=: read -r condition output_name routing_policy extra_flags <<<"${tasks[start + offset]}"
    echo "Starting ${condition} ${output_name}"
    run_one "${condition}" "${output_name}" "${routing_policy}" "${extra_flags}" &
    pids+=("$!")
    labels+=("${condition} ${output_name}")
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

echo "All ablation runs completed."
echo "Results: ${SCRIPT_DIR}/results"
