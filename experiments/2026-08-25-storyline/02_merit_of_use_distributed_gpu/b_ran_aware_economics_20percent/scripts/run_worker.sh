#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <cloud|apn> <pp: 1|2> <peak: 1-10>" >&2
  exit 2
fi

ENVIRONMENT="$1"
PP="$2"
PEAK="$3"
case "$ENVIRONMENT" in cloud|apn) ;; *) exit 2 ;; esac
case "$PP" in 1|2) ;; *) exit 2 ;; esac
case "$PEAK" in 1|2|3|4|5|6|7|8|9|10) ;; *) exit 2 ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/../../../.." && pwd)"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
PRIOR_EXP="experiments/2026-08-24-simulate-both-local-and-cloudlike"
THIS_EXP="experiments/2026-08-25-storyline/02_merit_of_use_distributed_gpu/b_ran_aware_economics_20percent"

if [ "$ENVIRONMENT" = "cloud" ]; then
  CONFIG="$THIS_EXP/configs/cloud_pp${PP}.json"
  GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
  BACKBONE_GBPS=183.68
  BACKBONE_LATENCY_NS=16800
else
  CONFIG="$THIS_EXP/configs/airan_active20_peak_pp${PP}.json"
  GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
  BACKBONE_GBPS=10.7
  BACKBONE_LATENCY_NS=300500
fi

if [ "$PP" -eq 1 ]; then
  if [ "$ENVIRONMENT" = "cloud" ]; then
    WORKLOAD="$SOURCE_EXP/workloads_clustered_cloud/hongo_peak_${PEAK}x_repeat600_seed1.jsonl"
  else
    WORKLOAD="$SOURCE_EXP/workloads_cloud_aligned/hongo_peak_${PEAK}x_repeat600_seed1.jsonl"
  fi
else
  WORKLOAD="$PRIOR_EXP/workloads_no_ran_pp2/hongo_peak_${PEAK}x_repeat600_seed1.jsonl"
fi

RUN_NAME="${ENVIRONMENT}_pp${PP}_peak_${PEAK}x_nearest_kv"
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
  --cluster-config "$CONFIG" \
  --dataset "$WORKLOAD" \
  --request-routing-policy NEAREST_KV \
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
  --inputs-root "/tmp/astra_runs/storyline_02b_active20_${RUN_NAME}" \
  --output "$OUTPUT/requests.csv" \
  --geographic-user-output "$OUTPUT/users.csv" \
  --geographic-gpu-output "$OUTPUT/gpus.csv" \
  --geographic-metadata-output "$OUTPUT/metadata.json" \
  --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
  --geographic-gpus-csv "$GPU_PLACEMENT" \
  --run-id "storyline-02b-active20-${RUN_NAME}" \
  --log-level WARNING \
  > "$LOG" 2>&1
echo "[$RUN_NAME complete]"
