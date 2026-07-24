#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
STOP_ON_FAILURE="${STOP_ON_FAILURE:-0}"
REQUEST_LIMIT="${REQUEST_LIMIT:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKLOAD="workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl"
POLICY="NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"
MANIFEST="${SCRIPT_DIR}/configs/counterfactual_manifest.csv"

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 12)); then
  echo "MAX_PARALLEL must be between 1 and 12." >&2
  exit 2
fi
if ((REQUEST_LIMIT < 0)); then
  echo "REQUEST_LIMIT must be zero or greater." >&2
  exit 2
fi
if [[ "${STOP_ON_FAILURE}" != "0" && "${STOP_ON_FAILURE}" != "1" ]]; then
  echo "STOP_ON_FAILURE must be 0 or 1." >&2
  exit 2
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/prepare_counterfactual_manifest.py"
mkdir -p "${SCRIPT_DIR}/counterfactual/results" \
  "${SCRIPT_DIR}/counterfactual/logs" "${SCRIPT_DIR}/counterfactual/status"

is_complete() {
  local case_id="$1"
  local output="${SCRIPT_DIR}/counterfactual/results/${case_id}/requests.csv"
  [[ -s "${output}" ]] && [[ "$(wc -l <"${output}")" -eq 301 ]]
}

run_case() {
  local case_id="$1"
  local request_id="$2"
  local target_id="$3"
  local output_dir="${SCRIPT_DIR}/counterfactual/results/${case_id}"
  local log_file="${SCRIPT_DIR}/counterfactual/logs/${case_id}.log"
  local status_file="${SCRIPT_DIR}/counterfactual/status/${case_id}.status"
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
      --counterfactual-request-id "${request_id}" \
      --counterfactual-target-instance-id "${target_id}" \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --run-id "prompt6000-90s-cf-${case_id}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if is_complete "${case_id}"; then
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
last_request_id=""
request_count=0
while IFS=, read -r case_id request_id target_id selected_by_model needs_simulation pressure_rank model_rank; do
  [[ "${case_id}" == "case_id" ]] && continue
  [[ "${needs_simulation}" == "1" ]] || continue
  if [[ "${request_id}" != "${last_request_id}" ]]; then
    request_count=$((request_count + 1))
    last_request_id="${request_id}"
  fi
  if ((REQUEST_LIMIT > 0 && request_count > REQUEST_LIMIT)); then
    continue
  fi
  if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${case_id}"; then
    echo "Skipping completed ${case_id}"
  else
    tasks+=("${case_id} ${request_id} ${target_id}")
  fi
done <"${MANIFEST}"

echo "Prepared ${#tasks[@]} counterfactual simulations with max parallelism ${MAX_PARALLEL}."
total_failed=0
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    read -r case_id request_id target_id <<<"${tasks[start + offset]}"
    echo "Starting ${case_id}"
    run_case "${case_id}" "${request_id}" "${target_id}" &
    pids+=("$!")
    labels+=("${case_id}")
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
      echo "Stopping after a failed counterfactual simulation." >&2
      exit 1
    fi
    echo "Continuing after a failed counterfactual simulation; inspect counterfactual/status and logs." >&2
  fi
done

echo "All candidate counterfactual simulations completed."
if ((total_failed)); then
  echo "One or more counterfactual simulations failed; rerun with SKIP_COMPLETED=1." >&2
  exit 1
fi
