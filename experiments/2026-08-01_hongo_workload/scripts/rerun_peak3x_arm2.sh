#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/app/LLMServingSim}"
EXP="experiments/2026-08-01_hongo_workload"
RUN_NAME="peak_3x_seed1_2_redirect_no_kv"
OUTDIR="$ROOT/$EXP/results/$RUN_NAME"
LOGFILE="$ROOT/$EXP/logs/${RUN_NAME}.log"

mkdir -p "$OUTDIR"

cd "$ROOT"

python3 -m serving \
  --cluster-config "$EXP/configs/rtx4090_hongo.json" \
  --dataset "$EXP/workloads/hongo_peak_3x_seed1.jsonl" \
  --request-routing-policy NEAREST_MIGRATE \
  --num-reqs 2000 \
  --max-num-seqs 128 \
  --max-num-batched-tokens 2048 \
  --gpu-backbone-bandwidth-gbps 10.7 \
  --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 \
  --kv-staging-latency-ns 102.9 \
  --inputs-root "/tmp/astra_runs/$RUN_NAME" \
  --output "$EXP/results/$RUN_NAME/requests.csv" \
  --geographic-user-output "$EXP/results/$RUN_NAME/users.csv" \
  --geographic-gpu-output "$EXP/results/$RUN_NAME/gpus.csv" \
  --geographic-metadata-output "$EXP/results/$RUN_NAME/metadata.json" \
  --geographic-users-csv "$EXP/placements/hongo_users.csv" \
  --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
  --run-id "hongo-peak_3x-seed1-2_redirect_no_kv" \
  --log-level WARNING \
  2>&1 | tee "$LOGFILE"
