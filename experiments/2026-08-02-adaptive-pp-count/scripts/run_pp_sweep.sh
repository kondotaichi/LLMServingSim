#!/usr/bin/env bash
# Sweep PP degree over the fixed 24-GPU Hongo pool, holding routing policy
# fixed at the best-performing arm found in 2026-08-01_hongo_workload
# (redirect + KV migration + proactive prewarm, i.e. that experiment's arm5):
#   --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE
#   --enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6
#   --proactive-kv-prewarm-top-k 3
#
# PP degrees are restricted to divisors of 24 (the fixed physical GPU pool)
# so every PP group has an equal GPU count: 1 2 3 4 6 8. (24 is not
# divisible by 5, so PP5 is skipped -- an uneven last group would bias that
# arm's comparison.)
set -uo pipefail

ROOT=/app/LLMServingSim
EXP=experiments/2026-08-02-adaptive-pp-count
SRC=experiments/2026-08-01_hongo_workload
MAX_PARALLEL="${MAX_PARALLEL:-2}"
NUM_REQS="${NUM_REQS:-2000}"
PP_SIZES="${PP_SIZES:-1 2 3 4 6 8}"

cd "$ROOT"

run_one() {
  local pp_size="$1"
  local run_name="pp${pp_size}"
  local outdir="$EXP/results/${run_name}"
  local cluster dataset
  mkdir -p "$outdir"

  if [ -f "$outdir/requests.csv" ] && [ "$(wc -l < "$outdir/requests.csv")" -ge $((NUM_REQS + 1)) ]; then
    echo "[skip] $run_name already complete"
    return 0
  fi

  if [ "$pp_size" -eq 1 ]; then
    cluster="$SRC/configs/rtx4090_hongo.json"
    dataset="$SRC/workloads/hongo_peak_3x_seed1.jsonl"
  else
    cluster="$EXP/configs/rtx4090_hongo_pp${pp_size}.json"
    dataset="$EXP/workloads/hongo_peak_3x_seed1_pp${pp_size}.jsonl"
    python3 "$EXP/scripts/make_pp_cluster_config.py" --pp-size "$pp_size" --output "$cluster"
    python3 "$EXP/scripts/transform_pp.py" \
      --input "$SRC/workloads/hongo_peak_3x_seed1.jsonl" \
      --output "$dataset" \
      --gpus-csv "$SRC/placements/hongo_gpus.csv" \
      --pp-size "$pp_size"
  fi

  local pp_flag=()
  if [ "$pp_size" -ne 1 ]; then
    pp_flag=(--pp-size "$pp_size")
  fi

  echo "[start] $run_name"
  python3 -m serving \
    --cluster-config "$cluster" \
    --dataset "$dataset" \
    --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
    --num-reqs "$NUM_REQS" \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --enable-proactive-kv-prewarm \
    --proactive-kv-prewarm-pressure-threshold 0.6 \
    --proactive-kv-prewarm-top-k 3 \
    --inputs-root "/tmp/astra_runs/adaptive_pp_${run_name}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$SRC/placements/hongo_users.csv" \
    --geographic-gpus-csv "$SRC/placements/hongo_gpus.csv" \
    --run-id "adaptive-pp-${run_name}" \
    --log-level WARNING \
    "${pp_flag[@]}" \
    > "$EXP/logs/${run_name}.log" 2>&1
  local rc=$?
  if [ $rc -eq 0 ]; then
    echo "[done] $run_name"
  else
    echo "[FAIL rc=$rc] $run_name"
  fi
  return $rc
}

running=0
pids=()
for pp_size in $PP_SIZES; do
  run_one "$pp_size" &
  pids+=($!)
  running=$((running + 1))

  if [ "$running" -ge "$MAX_PARALLEL" ]; then
    wait -n
    running=$((running - 1))
  fi
done

wait
echo "=== PP sweep finished (PP_SIZES=$PP_SIZES) ==="
