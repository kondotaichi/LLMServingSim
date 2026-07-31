#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKLOAD="workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl"
POLICY="NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"

# The 18 redirect decisions with full counterfactual candidate labels
# (experiments/2026-07-21-add_gpu_utilization/analysis/
# counterfactual_candidate_actuals_new_formula.csv). One force-local run per
# request -- unlike the candidate sweep (one arm per admissible GPU), "stay
# home" is a single arm, so this is 18 runs total, not ~9x18.
REQUEST_IDS=(49 51 67 82 84 90 154 156 161 165 173 175 197 203 205 263 265 267)

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 12)); then
  echo "MAX_PARALLEL must be between 1 and 12." >&2
  exit 2
fi

mkdir -p "${SCRIPT_DIR}/results" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

is_complete() {
  local request_id="$1"
  local output="${SCRIPT_DIR}/results/request${request_id}_force_local/requests.csv"
  [[ -s "${output}" ]] && [[ "$(wc -l <"${output}")" -eq 301 ]]
}

run_case() {
  local request_id="$1"
  local output_dir="${SCRIPT_DIR}/results/request${request_id}_force_local"
  local log_file="${SCRIPT_DIR}/logs/request${request_id}_force_local.log"
  local status_file="${SCRIPT_DIR}/status/request${request_id}_force_local.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --dataset "${WORKLOAD}" \
      --num-reqs 300 \
      --request-routing-policy "${POLICY}" \
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
      --counterfactual-force-local-request-id "${request_id}" \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --run-id "prompt6000-90s-force-local-${request_id}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if is_complete "${request_id}"; then
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
for request_id in "${REQUEST_IDS[@]}"; do
  if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${request_id}"; then
    echo "Skipping completed request${request_id}"
  else
    tasks+=("${request_id}")
  fi
done

echo "Prepared ${#tasks[@]} force-local counterfactual simulations with max parallelism ${MAX_PARALLEL}."
total_failed=0
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    request_id="${tasks[start + offset]}"
    echo "Starting request${request_id}"
    run_case "${request_id}" &
    pids+=("$!")
    labels+=("request${request_id}")
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
      echo "Stopping after a failed force-local simulation." >&2
      exit 1
    fi
    echo "Continuing after a failed force-local simulation; inspect status and logs." >&2
  fi
done

echo "All force-local counterfactual simulations completed."
if ((total_failed)); then
  echo "One or more simulations failed; rerun with SKIP_COMPLETED=1." >&2
  exit 1
fi
