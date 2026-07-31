#!/usr/bin/env bash
# Baseline vs Method-A-only at a given arrival-rate multiplier, PP1
# topology, to test whether a higher arrival rate creates enough genuine
# target-side congestion for Method A (scheduler-hide) to show real benefit.
set -euo pipefail

MULT="$1"   # e.g. 1.5, 2, 3 -- must match a generated workloads/input8000_reuse025_pp1_rate{MULT}x.jsonl

cd /app/LLMServingSim
WORKLOAD_DIR="experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_a_congestion_workload/workloads"
OUT_ROOT="experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_a_congestion_workload/results"
DATASET="${WORKLOAD_DIR}/input8000_reuse025_pp1_rate${MULT}x.jsonl"

run_one() {
  local arm="$1"       # baseline | a_only
  local extra_flags="$2"
  local out_dir="${OUT_ROOT}/rate${MULT}x_${arm}"
  mkdir -p "${out_dir}"
  TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  PYTHONUNBUFFERED=1 python3 -u -m serving \
    --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
    --pp-size 1 \
    --dataset "${DATASET}" \
    --num-reqs 300 \
    --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
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
    ${extra_flags} \
    --graph-converter in-process \
    --trace-io buffered \
    --output "${out_dir}/requests.csv" \
    --geographic-user-output "${out_dir}/users.csv" \
    --geographic-gpu-output "${out_dir}/gpus.csv" \
    --gpu-utilization-timeseries-output "${out_dir}/gpu_utilization_timeseries.csv" \
    --gpu-utilization-window-ns 1000000000 \
    --geographic-metadata-output "${out_dir}/metadata.json" \
    --run-id "congestion-probe-rate${MULT}x-${arm}" \
    --log-level WARNING > "experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_a_congestion_workload/logs/rate${MULT}x_${arm}.log" 2>&1
}

run_one baseline "" &
run_one a_only "--enable-scheduler-hide-kv-migration" &
wait
echo "rate${MULT}x probe done"
