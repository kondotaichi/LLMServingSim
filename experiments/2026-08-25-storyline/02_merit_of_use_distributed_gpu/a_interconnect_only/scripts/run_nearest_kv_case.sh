#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <pp: 1|2> <cloud|distributed> <peak-level: 1-10>" >&2
  exit 2
fi

PP="$1"
ENVIRONMENT="$2"
LEVEL="$3"

case "$PP" in 1|2) ;; *) echo "unsupported PP degree: $PP" >&2; exit 2 ;; esac
case "$ENVIRONMENT" in cloud|distributed) ;; *) echo "unsupported environment: $ENVIRONMENT" >&2; exit 2 ;; esac
case "$LEVEL" in 1|2|3|4|5|6|7|8|9|10) ;; *) echo "unsupported peak level: $LEVEL" >&2; exit 2 ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/../../../.." && pwd)"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
PRIOR_EXP="experiments/2026-08-24-simulate-both-local-and-cloudlike"

if [ "$PP" -eq 1 ]; then
  if [ "$ENVIRONMENT" = cloud ]; then
    CONFIG="$SOURCE_EXP/configs/gh200_8gpu_clustered_cloud.json"
    WORKLOAD_ROOT="$SOURCE_EXP/workloads_clustered_cloud"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
    BACKBONE_GBPS=183.68
    BACKBONE_LATENCY_NS=16800
  else
    CONFIG="$SOURCE_EXP/configs/gh200_hongo_8gpu_cloud_aligned.json"
    WORKLOAD_ROOT="$SOURCE_EXP/workloads_cloud_aligned"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    BACKBONE_GBPS=10.7
    BACKBONE_LATENCY_NS=300500
  fi
  WORKLOAD="$WORKLOAD_ROOT/hongo_peak_${LEVEL}x_repeat600_seed1.jsonl"
else
  if [ "$ENVIRONMENT" = cloud ]; then
    CONFIG="$PRIOR_EXP/configs/gh200_pp2_clustered_cloud.json"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
    BACKBONE_GBPS=183.68
    BACKBONE_LATENCY_NS=16800
  else
    CONFIG="$PRIOR_EXP/configs/gh200_pp2_distributed_apn.json"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    BACKBONE_GBPS=10.7
    BACKBONE_LATENCY_NS=300500
  fi
  WORKLOAD="$PRIOR_EXP/workloads_no_ran_pp2/hongo_peak_${LEVEL}x_repeat600_seed1.jsonl"
fi

cd "$REPO_ROOT"
RUN_NAME="pp${PP}_${ENVIRONMENT}_peak_${LEVEL}x_nearest_kv"
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

export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true
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
  --inputs-root "/tmp/astra_runs/storyline_02a_${RUN_NAME}" \
  --output "$OUTPUT/requests.csv" \
  --geographic-user-output "$OUTPUT/users.csv" \
  --geographic-gpu-output "$OUTPUT/gpus.csv" \
  --geographic-metadata-output "$OUTPUT/metadata.json" \
  --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
  --geographic-gpus-csv "$GPU_PLACEMENT" \
  --run-id "storyline-02a-${RUN_NAME}" \
  --log-level WARNING \
  > "$LOG" 2>&1

echo "[$RUN_NAME complete]"
