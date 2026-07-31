#!/usr/bin/env bash
# Quick single-arm (PP2 baseline, no Method A/B) probe run for a rate-
# rescaled workload, used to find an offered load that actually produces
# router/scheduler capacity pressure before committing to a full 4-arm
# sweep. Run inside the simulator container.
set -euo pipefail

WORKLOAD_JSONL="$1"   # path relative to repo root, e.g.
                       # experiments/2026-07-28_pp_schedule_conceal_and_speculative/workloads/input2000_reuse025_rate2x.jsonl
LABEL="$2"             # short label for output dir/run-id, e.g. rate2x

cd /app/LLMServingSim
OUT_DIR="experiments/2026-07-28_pp_schedule_conceal_and_speculative/results/probe_${LABEL}"
mkdir -p "${OUT_DIR}"

TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
PYTHONUNBUFFERED=1 python3 -u -m serving \
  --cluster-config configs/cluster/five_node_rtx4090_apn.json \
  --pp-size 2 \
  --dataset "${WORKLOAD_JSONL}" \
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
