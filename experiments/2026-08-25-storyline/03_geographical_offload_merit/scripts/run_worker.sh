#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <local_only|redirect_cold> <peak: 2-10>" >&2
  exit 2
fi

METHOD="$1"
PEAK="$2"
case "$METHOD" in local_only|redirect_cold) ;; *) exit 2 ;; esac
case "$PEAK" in 2|3|4|5|6|7|8|9|10) ;; *) exit 2 ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/../../.." && pwd)"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
THIS_EXP="experiments/2026-08-25-storyline/03_geographical_offload_merit"

if [ "$METHOD" = "local_only" ]; then
  POLICY="NEAREST_KV"
else
  POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE"
fi

RUN_NAME="peak_${PEAK}x_${METHOD}_ai70_30"
OUTPUT="$EXP_ROOT/results/$RUN_NAME"
LOG="$EXP_ROOT/logs/$RUN_NAME.log"
mkdir -p "$OUTPUT" "$EXP_ROOT/logs"

if [ -f "$OUTPUT/requests.csv" ]; then
  ROWS="$(awk 'END {print NR - 1}' "$OUTPUT/requests.csv")"
  if [ "$ROWS" -eq 600 ]; then
    echo "[$RUN_NAME skip complete]"
    exit 0
  fi
fi

cd "$REPO_ROOT"
export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true
echo "[$RUN_NAME start]"
python3 -m serving \
  --cluster-config "$THIS_EXP/configs/airan_hongo_imbalanced_active20_pp1.json" \
  --dataset "$THIS_EXP/workloads_ai_70_30/hongo_peak_${PEAK}x_repeat600_seed1_ai70_30.jsonl" \
  --request-routing-policy "$POLICY" \
  --num-reqs 600 \
  --max-num-seqs 1024 \
  --max-num-batched-tokens 8192 \
  --block-size 16 \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps 10.7 \
  --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 297.8 \
  --kv-staging-latency-ns 2650 \
  --inputs-root "/tmp/astra_runs/storyline_03_${RUN_NAME}" \
  --output "$OUTPUT/requests.csv" \
  --geographic-user-output "$OUTPUT/users.csv" \
  --geographic-gpu-output "$OUTPUT/gpus.csv" \
  --geographic-metadata-output "$OUTPUT/metadata.json" \
  --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
  --geographic-gpus-csv "$SOURCE_EXP/placements/hongo_gh200_gpus.csv" \
  --run-id "storyline-03-${RUN_NAME}" \
  --log-level WARNING \
  > "$LOG" 2>&1
echo "[$RUN_NAME complete]"
