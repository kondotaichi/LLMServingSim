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
PREFIX="peak_${LEVEL}x_repeat600_seed1"
NUM_REQS="${NUM_REQS:-600}"
METHOD_FILTER="${METHOD_FILTER:-}"

cd "$ROOT"
mkdir -p "$EXP/results" "$EXP/logs"

run_one() {
  local number="$1" name="$2" policy="$3" cluster="$4" dataset="$5"
  shift 5
  local output="$EXP/results/${PREFIX}_${number}_${name}"
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
    --cluster-config "$EXP/configs/$cluster" \
    --dataset "$EXP/workloads/$dataset" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --block-size 16 \
    --dtype bfloat16 \
    --kv-cache-dtype auto \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 297.8 \
    --kv-staging-latency-ns 2650 \
    --inputs-root "/tmp/astra_runs/gh200_${PREFIX}_${number}_${name}" \
    --output "$output/requests.csv" \
    --geographic-user-output "$output/users.csv" \
    --geographic-gpu-output "$output/gpus.csv" \
    --geographic-metadata-output "$output/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users_8gpu.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gh200_gpus.csv" \
    --run-id "hongo-gh200-${PREFIX}-${number}-${name}" \
    --log-level WARNING \
    "$@" \
    > "$EXP/logs/${PREFIX}_${number}_${name}.log" 2>&1
  local rc=$?
  echo "[Peak ${LEVEL}x done rc=$rc] ${number}_${name}"
  return "$rc"
}

failed=0
if [ -z "$METHOD_FILTER" ] || [ "$METHOD_FILTER" = 1 ]; then
  run_one 1 naive NEAREST_KV gh200_hongo_8gpu.json \
    "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
fi
if [ -z "$METHOD_FILTER" ] || [ "$METHOD_FILTER" = 2 ]; then
  run_one 2 redirect_no_kv NEAREST_MIGRATE gh200_hongo_8gpu.json \
    "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
fi
if [ -z "$METHOD_FILTER" ] || [ "$METHOD_FILTER" = 3 ]; then
  run_one 3 redirect_kv_no_pp NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
    gh200_hongo_8gpu.json "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
fi
if [ -z "$METHOD_FILTER" ] || [ "$METHOD_FILTER" = 4 ]; then
  run_one 4 redirect_kv_pp2 NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
    gh200_hongo_8gpu_pp2.json "hongo_peak_${LEVEL}x_repeat600_seed1_pp2.jsonl" || failed=1
fi

exit "$failed"
