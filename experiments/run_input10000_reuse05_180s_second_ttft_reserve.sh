#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

EXPERIMENT_DIR="experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果"
OUTPUT_DIR="${EXPERIMENT_DIR}/results/NEAREST_SECOND_TTFT_RESERVE_T160K_I1M"

mkdir -p "${OUTPUT_DIR}"

python -m serving \
  --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
  --dataset "${EXPERIMENT_DIR}/workloads/input10000_reuse05_180s.jsonl" \
  --num-reqs 300 \
  --request-routing-policy NEAREST_SECOND_TTFT_RESERVE \
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
  --second-ttft-reserve-token-time-ns 160000 \
  --second-ttft-reserve-iteration-time-ns 1000000 \
  --graph-converter in-process \
  --trace-io buffered \
  --output "${OUTPUT_DIR}/requests.csv" \
  --geographic-user-output "${OUTPUT_DIR}/users.csv" \
  --geographic-gpu-output "${OUTPUT_DIR}/gpus.csv" \
  --geographic-metadata-output "${OUTPUT_DIR}/metadata.json" \
  --geographic-users-csv workloads/generated/cell_apn/users.csv \
  --geographic-gpus-csv workloads/generated/cell_apn/gpus.csv \
  --run-id input10000-reuse05-180s-NEAREST-SECOND-TTFT-RESERVE-T160K-I1M \
  --log-level WARNING
