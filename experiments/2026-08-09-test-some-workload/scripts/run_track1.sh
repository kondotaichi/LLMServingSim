#!/usr/bin/env bash
# Run one or more matched PP x KV arms for a selected load multiplier.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
HONGO_EXP="experiments/2026-08-01_hongo_workload"
EXP="experiments/2026-08-09-test-some-workload"
LOAD="${LOAD:-12}"
NUM_REQS="${NUM_REQS:-300}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-128}"
MAX_BATCHED_TOKENS="${MAX_BATCHED_TOKENS:-2048}"
ARM_FILTER="${ARM_FILTER:-pp1_cold,pp1_kv,pp2_cold,pp2_kv}"
FULL_WORKLOAD="${FULL_WORKLOAD:-0}"
RUN_TAG="${RUN_TAG:-load_${LOAD}x}"
DATASET_OVERRIDE="${DATASET_OVERRIDE:-}"

cd "$ROOT"
mkdir -p "$EXP/results/$RUN_TAG" "$EXP/logs"

run_one() {
  local arm="$1" pp="$2" policy="$3"
  local workload cluster pp_flag
  local workload_root="$EXP/workloads"
  local config_root="$HONGO_EXP/configs"
  if [ "$FULL_WORKLOAD" = "1" ]; then
    workload_root="$EXP/workloads/full"
    config_root="$EXP/configs"
  fi
  if [ "$pp" -eq 2 ]; then
    if [ "$FULL_WORKLOAD" = "1" ]; then
      workload="$workload_root/hongo_peak_${LOAD}x_seed1_pp2.jsonl"
    else
      workload="$workload_root/pp2/load_${LOAD}x.jsonl"
    fi
    cluster="$config_root/rtx4090_hongo_pp2.json"
    pp_flag="--pp-size 2"
  else
    if [ "$FULL_WORKLOAD" = "1" ]; then
      workload="$workload_root/hongo_peak_${LOAD}x_seed1.jsonl"
    else
      workload="$workload_root/pp1/load_${LOAD}x.jsonl"
    fi
    cluster="$config_root/rtx4090_hongo.json"
    pp_flag=""
  fi
  if [ -n "$DATASET_OVERRIDE" ]; then
    workload="$DATASET_OVERRIDE"
  fi
  local outdir="$EXP/results/$RUN_TAG/$arm"
  mkdir -p "$outdir"
  echo "[start] load_${LOAD}x $arm"
  # shellcheck disable=SC2086
  python3 -m serving \
    --cluster-config "$cluster" \
    --dataset "$workload" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --max-num-batched-tokens "$MAX_BATCHED_TOKENS" \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/method_fix_load_${LOAD}x_${arm}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$([ "$FULL_WORKLOAD" = "1" ] && echo "$EXP" || echo "$HONGO_EXP")/placements/hongo_users.csv" \
    --geographic-gpus-csv "$([ "$FULL_WORKLOAD" = "1" ] && echo "$EXP" || echo "$HONGO_EXP")/placements/hongo_gpus.csv" \
    --run-id "method-fix-load-${LOAD}x-${arm}" \
    --log-level WARNING \
    $pp_flag \
    > "$EXP/logs/${RUN_TAG}_${arm}.log" 2>&1
  echo "[done] load_${LOAD}x $arm"
}

for spec in \
  "pp1_cold|1|NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE" \
  "pp1_kv|1|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE" \
  "pp2_cold|2|NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE" \
  "pp2_kv|2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
do
  IFS='|' read -r arm pp policy <<< "$spec"
  if [[ ",$ARM_FILTER," != *",$arm,"* ]]; then
    continue
  fi
  run_one "$arm" "$pp" "$policy"
done
