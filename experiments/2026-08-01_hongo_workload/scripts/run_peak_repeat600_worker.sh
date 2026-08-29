#!/usr/bin/env bash
# Run four methods sequentially for one repeated Peak workload.

set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <peak-level: 1-4>" >&2
  exit 2
fi

LEVEL="$1"
case "$LEVEL" in
  1|2|3|4) ;;
  *) echo "unsupported peak level: $LEVEL" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-01_hongo_workload"
PREFIX="peak_${LEVEL}x_repeat600_seed1"

cd "$ROOT"
mkdir -p "$EXP/results" "$EXP/logs"

run_one() {
  local number="$1" name="$2" policy="$3" cluster="$4" dataset="$5"
  shift 5
  local output="$EXP/results/${PREFIX}_${number}_${name}"
  mkdir -p "$output"
  echo "[Peak ${LEVEL}x start] ${number}_${name}"
  python3 -m serving \
    --cluster-config "$EXP/configs/$cluster" \
    --dataset "$EXP/workloads/$dataset" \
    --request-routing-policy "$policy" \
    --num-reqs 600 \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/${PREFIX}_${number}_${name}" \
    --output "$output/requests.csv" \
    --geographic-user-output "$output/users.csv" \
    --geographic-gpu-output "$output/gpus.csv" \
    --geographic-metadata-output "$output/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
    --run-id "hongo-${PREFIX}-${number}-${name}" \
    --log-level WARNING \
    "$@" \
    > "$EXP/logs/${PREFIX}_${number}_${name}.log" 2>&1
  local rc=$?
  echo "[Peak ${LEVEL}x done rc=$rc] ${number}_${name}"
  return "$rc"
}

failed=0
run_one 1 no_redirect NEAREST_KV rtx4090_hongo.json \
  "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
run_one 2 redirect_no_kv NEAREST_MIGRATE rtx4090_hongo.json \
  "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
run_one 3 redirect_kv_nopp NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  rtx4090_hongo.json "hongo_peak_${LEVEL}x_repeat600_seed1.jsonl" || failed=1
run_one 4 redirect_kv_pp2 NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
  rtx4090_hongo_pp2.json "hongo_peak_${LEVEL}x_repeat600_seed1_pp2.jsonl" \
  --pp-size 2 || failed=1

exit "$failed"
