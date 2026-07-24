#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

POLICY="NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE"
RESULT_NAME="${POLICY}_M200MS_D1S_PHASE1_MODEL20260720"
PYTHON_BIN="${PYTHON_BIN:-python3}"

run_condition() {
  local experiment_dir="$1"
  local workload="$2"
  local run_id="$3"
  local output_dir="${experiment_dir}/results/${RESULT_NAME}"

  mkdir -p "${output_dir}" "${experiment_dir}/logs"
  env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true "${PYTHON_BIN}" -m serving \
    --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
    --dataset "${workload}" \
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
    --graph-converter in-process \
    --trace-io buffered \
    --output "${output_dir}/requests.csv" \
    --geographic-user-output "${output_dir}/users.csv" \
    --geographic-gpu-output "${output_dir}/gpus.csv" \
    --geographic-metadata-output "${output_dir}/metadata.json" \
    --geographic-users-csv workloads/generated/cell_apn/users.csv \
    --geographic-gpus-csv workloads/generated/cell_apn/gpus.csv \
    --run-id "${run_id}" \
    --log-level WARNING
}

run_condition \
  "experiments/2026-07-14_prompt6000_90s_three_policy_良結果" \
  "workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl" \
  "prompt6000-reuse50-90s-formula-phase1-model-m200ms-d1s" \
  > "experiments/2026-07-14_prompt6000_90s_three_policy_良結果/logs/formula_phase1_model.log" 2>&1 &
PID_PROMPT=$!

run_condition \
  "experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果" \
  "experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果/workloads/input10000_reuse05_180s.jsonl" \
  "input10000-reuse05-180s-formula-phase1-model-m200ms-d1s" \
  > "experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果/logs/formula_phase1_model.log" 2>&1 &
PID_INPUT=$!

STATUS_PROMPT=0
STATUS_INPUT=0
wait "${PID_PROMPT}" || STATUS_PROMPT=$?
wait "${PID_INPUT}" || STATUS_INPUT=$?

echo "prompt6000 status: ${STATUS_PROMPT}"
echo "input10000 status: ${STATUS_INPUT}"

if ((STATUS_PROMPT != 0 || STATUS_INPUT != 0)); then
  exit 1
fi
