#!/usr/bin/env bash
# Run one Kagoshima-Tokyo baseline to select an offered-load point.
set -euo pipefail

EXP="experiments/2026-08-02-kagosima-tokyo"
LOAD="${LOAD:-original}"
LOAD_LABEL="${LOAD//./p}"
RUN_NAME="pressure-probe-${LOAD_LABEL}-kg-pp2"
OUT="${EXP}/results/probes/${LOAD_LABEL}/kg_pp2"
LOG="${EXP}/logs/${RUN_NAME}.log"
DATASET="${EXP}/workloads/kagoshima_tokyo_pp2/reference_${LOAD_LABEL}_seed1.jsonl"

mkdir -p "${OUT}" "${EXP}/logs"

TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
PYTHONUNBUFFERED=1 python3 -u -m serving \
  --cluster-config "${EXP}/configs/kagoshima_tokyo_12gpu.json" \
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
  --enable-scheduler-hide-kv-migration \
  --pp-size 2 \
  --graph-converter in-process \
  --trace-io buffered \
  --inputs-root "/tmp/astra_runs/${RUN_NAME}" \
  --output "${OUT}/requests.csv" \
  --geographic-user-output "${OUT}/users.csv" \
  --geographic-gpu-output "${OUT}/gpus.csv" \
  --geographic-metadata-output "${OUT}/metadata.json" \
  --geographic-users-csv "${EXP}/placements/kagoshima_tokyo_users.csv" \
  --geographic-gpus-csv "${EXP}/placements/kagoshima_tokyo_gpus.csv" \
  --gpu-utilization-timeseries-output "${OUT}/gpu_utilization_timeseries.csv" \
  --gpu-utilization-window-ns 1000000000 \
  --run-id "${RUN_NAME}" \
  --log-level WARNING \
  >"${LOG}" 2>&1

python3 "${EXP}/scripts/summarize_pressure_run.py" "${OUT}/requests.csv"
