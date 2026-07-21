#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

WORKLOAD="experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果/workloads/input10000_reuse05_180s.jsonl"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "${LOG_DIR}"

run_policy() {
  local policy="$1"
  local result_name="$2"
  local output_dir="${SCRIPT_DIR}/results/${result_name}"
  mkdir -p "${output_dir}"

  env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true python3 -m serving \
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
    --geographic-metadata-output "${output_dir}/metadata.json" \
    --geographic-users-csv workloads/generated/cell_apn/users.csv \
    --geographic-gpus-csv workloads/generated/cell_apn/gpus.csv \
    --run-id "input10000-reuse05-180s-${result_name}" \
    --log-level WARNING
}

DYNAMIC_RESULT="NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"
MULTI_RESULT="NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"

run_policy "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE" "${DYNAMIC_RESULT}" \
  > "${LOG_DIR}/formula_dynamic.log" 2>&1 &
PID_DYNAMIC=$!

run_policy "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE" "${MULTI_RESULT}" \
  > "${LOG_DIR}/formula_multi.log" 2>&1 &
PID_MULTI=$!

STATUS_DYNAMIC=0
STATUS_MULTI=0
wait "${PID_DYNAMIC}" || STATUS_DYNAMIC=$?
wait "${PID_MULTI}" || STATUS_MULTI=$?

echo "dynamic status: ${STATUS_DYNAMIC}"
echo "multi status: ${STATUS_MULTI}"

if ((STATUS_DYNAMIC != 0 || STATUS_MULTI != 0)); then
  exit 1
fi
