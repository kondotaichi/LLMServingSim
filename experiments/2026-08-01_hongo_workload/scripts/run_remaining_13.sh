#!/usr/bin/env bash
# Run the 13 still-missing (load_level, arm) simulations from the 5-arm
# Hongo comparison (peak_2x arms 1,2,4,5 / peak_2p5x arms 1,2,4,5 /
# peak_3x arms 1-5). Arm 3 already completed for peak_2x and peak_2p5x
# and is skipped. Runs with bounded parallelism (MAX_PARALLEL, default 3)
# to avoid the Docker Desktop crashes seen at 5-way parallelism.
set -uo pipefail

ROOT=/app/LLMServingSim
EXP=experiments/2026-08-01_hongo_workload
MAX_PARALLEL="${MAX_PARALLEL:-3}"

cd "$ROOT"

run_one() {
  local level="$1" arm_num="$2" arm_name="$3" policy="$4" cluster="$5" dataset="$6"
  shift 6
  local extra_flags=("$@")
  local run_name="${level}_seed1_${arm_num}_${arm_name}"
  local outdir="$EXP/results/${run_name}"
  mkdir -p "$outdir"

  if [ -f "$outdir/requests.csv" ] && [ "$(wc -l < "$outdir/requests.csv")" -ge 2001 ]; then
    echo "[skip] $run_name already complete"
    return 0
  fi

  echo "[start] $run_name"
  python3 -m serving \
    --cluster-config "$EXP/configs/${cluster}" \
    --dataset "$EXP/workloads/${dataset}" \
    --request-routing-policy "$policy" \
    --num-reqs 2000 \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
    --run-id "hongo-${level}-seed1-${arm_num}_${arm_name}" \
    --log-level WARNING \
    "${extra_flags[@]}" \
    > "$EXP/logs/${run_name}.log" 2>&1
  local rc=$?
  if [ $rc -eq 0 ]; then
    echo "[done] $run_name"
  else
    echo "[FAIL rc=$rc] $run_name"
  fi
  return $rc
}

declare -a JOBS=()
for level in peak_3x; do
  # arm1: no redirect
  JOBS+=("$level|1|no_redirect|NEAREST_KV|rtx4090_hongo.json|hongo_${level}_seed1.jsonl|")
  # arm2: redirect, no KV migration
  JOBS+=("$level|2|redirect_no_kv|NEAREST_MIGRATE|rtx4090_hongo.json|hongo_${level}_seed1.jsonl|")
  # arm3: redirect + KV migration, PP1
  JOBS+=("$level|3|redirect_kv_nopp|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_hongo.json|hongo_${level}_seed1.jsonl|")
  # arm4: redirect + KV migration, PP2
  JOBS+=("$level|4|redirect_kv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_hongo_pp2.json|hongo_${level}_seed1_pp2.jsonl|--pp-size 2")
  # arm5: redirect + KV migration, PP2, Method C proactive prewarm (production th=0.6/k=3)
  JOBS+=("$level|5|redirect_kv_pp2_c|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_hongo_pp2.json|hongo_${level}_seed1_pp2.jsonl|--pp-size 2 --enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3")
done

running=0
pids=()
for job in "${JOBS[@]}"; do
  IFS='|' read -r level arm_num arm_name policy cluster dataset extra <<< "$job"
  # shellcheck disable=SC2206
  extra_flags=($extra)

  run_one "$level" "$arm_num" "$arm_name" "$policy" "$cluster" "$dataset" "${extra_flags[@]}" &
  pids+=($!)
  running=$((running + 1))

  if [ "$running" -ge "$MAX_PARALLEL" ]; then
    wait -n
    running=$((running - 1))
  fi
done

wait
echo "=== all peak_3x jobs finished ==="
