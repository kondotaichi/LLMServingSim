#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -eq 3 ]; then
  ENVIRONMENT="$1"
  shift
else
  ENVIRONMENT="distributed_normal_wan"
fi
if [ "$#" -ne 2 ]; then
  echo "usage: $0 [clustered_cloud|distributed_normal_wan|distributed_apn] <peak-level: 1-10> <nearest_kv|load>" >&2
  exit 2
fi

LEVEL="$1"
METHOD="$2"
case "$LEVEL" in 1|2|3|4|5|6|7|8|9|10) ;; *) exit 2 ;; esac
case "$METHOD" in
  nearest_kv) NUMBER=1; POLICY=NEAREST_KV ;;
  load) NUMBER=2; POLICY=LOAD ;;
  *) exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-24-simulate-both-local-and-cloudlike"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
case "$ENVIRONMENT" in
  clustered_cloud)
    CONFIG="$EXP/configs/gh200_pp2_clustered_cloud.json"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
    BACKBONE_GBPS=183.68
    BACKBONE_LATENCY_NS=16800
    ;;
  distributed_normal_wan)
    CONFIG="$EXP/configs/gh200_pp2_distributed_normal_wan.json"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    BACKBONE_GBPS=1
    BACKBONE_LATENCY_NS=5000000
    ;;
  distributed_apn)
    CONFIG="$EXP/configs/gh200_pp2_distributed_apn.json"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    BACKBONE_GBPS=10.7
    BACKBONE_LATENCY_NS=300500
    ;;
  *) exit 2 ;;
esac
RUN_NAME="peak_${LEVEL}x_repeat600_seed1_pp2_${NUMBER}_${METHOD}"
OUTPUT="$EXP/results_no_ran_pp_exist/$ENVIRONMENT/$RUN_NAME"
LOG="$EXP/logs_no_ran_pp_exist/$ENVIRONMENT/$RUN_NAME.log"
INPUTS_ROOT="/tmp/astra_runs/gh200_pp2_${ENVIRONMENT}_${RUN_NAME}"

cd "$ROOT"
mkdir -p "$OUTPUT" "$EXP/logs_no_ran_pp_exist/$ENVIRONMENT"
export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true

if [ -f "$OUTPUT/requests.csv" ]; then
  ROWS="$(awk 'END {print NR - 1}' "$OUTPUT/requests.csv")"
  if [ "$ROWS" -eq 600 ]; then
    echo "[$ENVIRONMENT Peak ${LEVEL}x skip] ${NUMBER}_${METHOD}"
    exit 0
  fi
fi

echo "[$ENVIRONMENT Peak ${LEVEL}x start] ${NUMBER}_${METHOD}"
python3 -m serving \
  --cluster-config "$CONFIG" \
  --dataset "$EXP/workloads_no_ran_pp2/hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" \
  --request-routing-policy "$POLICY" \
  --num-reqs 600 \
  --max-num-seqs 1024 \
  --max-num-batched-tokens 8192 \
  --block-size 16 \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --gpu-backbone-bandwidth-gbps "$BACKBONE_GBPS" \
  --apn-fixed-propagation-ns "$BACKBONE_LATENCY_NS" \
  --kv-staging-bandwidth-gbytes-per-s 297.8 \
  --kv-staging-latency-ns 2650 \
  --inputs-root "$INPUTS_ROOT" \
  --output "$OUTPUT/requests.csv" \
  --geographic-user-output "$OUTPUT/users.csv" \
  --geographic-gpu-output "$OUTPUT/gpus.csv" \
  --geographic-metadata-output "$OUTPUT/metadata.json" \
  --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
  --geographic-gpus-csv "$GPU_PLACEMENT" \
  --run-id "gh200-pp2-${ENVIRONMENT}-${RUN_NAME}" \
  --log-level WARNING \
  > "$LOG" 2>&1
RC=$?
rm -rf "$INPUTS_ROOT"
echo "[$ENVIRONMENT Peak ${LEVEL}x done rc=$RC] ${NUMBER}_${METHOD}"
exit "$RC"
