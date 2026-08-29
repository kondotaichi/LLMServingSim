#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <peak-level: 1-10>" >&2
  exit 2
fi
LEVEL="$1"
case "$LEVEL" in
  1|2|3|4|5|6|7|8|9|10) ;;
  *) echo "unsupported peak level: $LEVEL" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
NUM_REQS="${NUM_REQS:-600}"

cd "$ROOT"
mkdir -p "$EXP/results_clustered_cloud" "$EXP/logs_clustered_cloud"
export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true

run_one() {
  local number="$1" name="$2" policy="$3"
  local run_name="peak_${LEVEL}x_repeat600_seed1_clustered_cloud_${number}_${name}"
  local output="$EXP/results_clustered_cloud/$run_name"
  local log="$EXP/logs_clustered_cloud/$run_name.log"
  mkdir -p "$output"
  if [ -f "$output/requests.csv" ]; then
    local completed_rows
    completed_rows="$(awk 'END {print NR - 1}' "$output/requests.csv")"
    if [ "$completed_rows" -eq "$NUM_REQS" ]; then
      echo "[Peak ${LEVEL}x skip complete] ${number}_${name} (${completed_rows} requests)"
      return 0
    fi
  fi

  echo "[Peak ${LEVEL}x start] ${number}_${name}"
  python3 -m serving \
    --cluster-config "$EXP/configs/gh200_8gpu_clustered_cloud.json" \
    --dataset "$EXP/workloads_clustered_cloud/hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
    --max-num-seqs 1024 \
    --max-num-batched-tokens 8192 \
    --block-size 16 \
    --dtype bfloat16 \
    --kv-cache-dtype auto \
    --enable-chunked-prefill \
    --enable-prefix-caching \
    --gpu-backbone-bandwidth-gbps 183.68 \
    --apn-fixed-propagation-ns 16800 \
    --kv-staging-bandwidth-gbytes-per-s 297.8 \
    --kv-staging-latency-ns 2650 \
    --inputs-root "/tmp/astra_runs/gh200_${run_name}" \
    --output "$output/requests.csv" \
    --geographic-user-output "$output/users.csv" \
    --geographic-gpu-output "$output/gpus.csv" \
    --geographic-metadata-output "$output/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users_8gpu.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gh200_gpus_clustered.csv" \
    --run-id "hongo-gh200-${run_name}" \
    --log-level WARNING \
    > "$log" 2>&1
  local rc=$?
  echo "[Peak ${LEVEL}x done rc=$rc] ${number}_${name}"
  return "$rc"
}

failed=0
run_one 1 nearest_kv NEAREST_KV || failed=1
run_one 2 load LOAD || failed=1
exit "$failed"
