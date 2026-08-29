#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <distributed|clustered_cloud> <peak-level: 1-10>" >&2
  exit 2
fi

ENVIRONMENT="$1"
LEVEL="$2"
case "$LEVEL" in
  1|2|3|4|5|6|7|8|9|10) ;;
  *) echo "unsupported peak level: $LEVEL" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
EXP="experiments/2026-08-24-simulate-both-local-and-cloudlike"
NUM_REQS="${NUM_REQS:-600}"

case "$ENVIRONMENT" in
  distributed)
    CLUSTER_CONFIG="$SOURCE_EXP/configs/gh200_hongo_8gpu_cloud_aligned.json"
    WORKLOAD_ROOT="$SOURCE_EXP/workloads_cloud_aligned"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    BACKBONE_GBPS="10.7"
    BACKBONE_LATENCY_NS="300500"
    ;;
  clustered_cloud)
    CLUSTER_CONFIG="$SOURCE_EXP/configs/gh200_8gpu_clustered_cloud.json"
    WORKLOAD_ROOT="$SOURCE_EXP/workloads_clustered_cloud"
    GPU_PLACEMENT="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
    BACKBONE_GBPS="183.68"
    BACKBONE_LATENCY_NS="16800"
    ;;
  *)
    echo "unsupported environment: $ENVIRONMENT" >&2
    exit 2
    ;;
esac

cd "$ROOT"
mkdir -p "$EXP/results/$ENVIRONMENT" "$EXP/logs/$ENVIRONMENT"
export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true

run_one() {
  local number="$1" name="$2" policy="$3"
  local run_name="peak_${LEVEL}x_repeat600_seed1_${number}_${name}"
  local output="$EXP/results/$ENVIRONMENT/$run_name"
  local log="$EXP/logs/$ENVIRONMENT/$run_name.log"
  mkdir -p "$output"

  if [ -f "$output/requests.csv" ]; then
    local completed_rows
    completed_rows="$(awk 'END {print NR - 1}' "$output/requests.csv")"
    if [ "$completed_rows" -eq "$NUM_REQS" ]; then
      echo "[$ENVIRONMENT Peak ${LEVEL}x skip complete] ${number}_${name}"
      return 0
    fi
  fi

  echo "[$ENVIRONMENT Peak ${LEVEL}x start] ${number}_${name}"
  python3 -m serving \
    --cluster-config "$CLUSTER_CONFIG" \
    --dataset "$WORKLOAD_ROOT/hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
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
    --inputs-root "/tmp/astra_runs/gh200_${ENVIRONMENT}_${run_name}" \
    --output "$output/requests.csv" \
    --geographic-user-output "$output/users.csv" \
    --geographic-gpu-output "$output/gpus.csv" \
    --geographic-metadata-output "$output/metadata.json" \
    --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
    --geographic-gpus-csv "$GPU_PLACEMENT" \
    --run-id "gh200-${ENVIRONMENT}-${run_name}" \
    --log-level WARNING \
    > "$log" 2>&1
  local rc=$?
  echo "[$ENVIRONMENT Peak ${LEVEL}x done rc=$rc] ${number}_${name}"
  return "$rc"
}

failed=0
run_one 1 nearest_kv NEAREST_KV || failed=1
run_one 2 load LOAD || failed=1
exit "$failed"
