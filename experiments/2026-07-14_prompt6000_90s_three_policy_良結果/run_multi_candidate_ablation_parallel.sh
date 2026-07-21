#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

WORKLOAD="workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl"
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
    --run-id "prompt6000-90s-${result_name}" \
    --log-level WARNING
}

WAITING_RESULT="NEAREST_CAPACITY_MULTI_WAITING_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"
PRESSURE_RESULT="NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"
RANDOM_RESULT="NEAREST_CAPACITY_MULTI_RANDOM_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"

run_policy "NEAREST_CAPACITY_MULTI_WAITING_FORMULA_KV_RESERVE" "${WAITING_RESULT}" \
  > "${LOG_DIR}/multi_ablation_waiting.log" 2>&1 &
PID_WAITING=$!

run_policy "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE" "${PRESSURE_RESULT}" \
  > "${LOG_DIR}/multi_ablation_pressure.log" 2>&1 &
PID_PRESSURE=$!

run_policy "NEAREST_CAPACITY_MULTI_RANDOM_FORMULA_KV_RESERVE" "${RANDOM_RESULT}" \
  > "${LOG_DIR}/multi_ablation_random.log" 2>&1 &
PID_RANDOM=$!

STATUS_WAITING=0
STATUS_PRESSURE=0
STATUS_RANDOM=0
wait "${PID_WAITING}" || STATUS_WAITING=$?
wait "${PID_PRESSURE}" || STATUS_PRESSURE=$?
wait "${PID_RANDOM}" || STATUS_RANDOM=$?

echo "waiting status: ${STATUS_WAITING}"
echo "pressure status: ${STATUS_PRESSURE}"
echo "random status: ${STATUS_RANDOM}"

if ((STATUS_WAITING != 0 || STATUS_PRESSURE != 0 || STATUS_RANDOM != 0)); then
  exit 1
fi
