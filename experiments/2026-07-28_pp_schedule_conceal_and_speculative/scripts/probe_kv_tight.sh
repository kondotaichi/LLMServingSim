#!/usr/bin/env bash
# Quick single-arm (PP2 baseline, no Method A/B) probe run against a
# KV-budget-tightened cluster config, to check whether shrinking npu_mem
# (the confirmed binding constraint behind existing PP2 redirects --
# redirect_capacity_reason=npu_memory, not seq-count) produces meaningfully
# more capacity-pressure redirects without touching workload/arrival data.
set -euo pipefail

WORKLOAD="$1"        # e.g. input8000_reuse025
CLUSTER_CONFIG="$2"  # path relative to repo root
LABEL="$3"           # short label for output dir/run-id

cd /app/LLMServingSim
DATASET_DIR="experiments/2026-07-22_pp2_five_workloads/workloads"
OUT_DIR="experiments/2026-07-28_pp_schedule_conceal_and_speculative/results/probe_${LABEL}"
mkdir -p "${OUT_DIR}"

TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
PYTHONUNBUFFERED=1 python3 -u -m serving \
  --cluster-config "${CLUSTER_CONFIG}" \
  --pp-size 2 \
  --dataset "${DATASET_DIR}/${WORKLOAD}.jsonl" \
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
  --graph-converter in-process \
  --trace-io buffered \
  --output "${OUT_DIR}/requests.csv" \
  --geographic-user-output "${OUT_DIR}/users.csv" \
  --geographic-gpu-output "${OUT_DIR}/gpus.csv" \
  --gpu-utilization-timeseries-output "${OUT_DIR}/gpu_utilization_timeseries.csv" \
  --gpu-utilization-window-ns 1000000000 \
  --geographic-metadata-output "${OUT_DIR}/metadata.json" \
  --run-id "pp2-probe-${LABEL}" \
  --log-level WARNING
