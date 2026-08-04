#!/usr/bin/env bash
# Run the unbalanced-workload six-arm comparison. Same harness as the balanced
# Hongo probe, but on the spatially-imbalanced workload and with an extra arm
# (arm 5: PP2 + redirect WITHOUT KV migration) so the pure KV-migration benefit
# under pipeline parallelism can be isolated: arm4 (PP2+KV) vs arm5 (PP2, no KV).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../../" && pwd)"
EXP="experiments/2026-08-04-umbalanced_workload"
LOAD_LEVEL="${LOAD_LEVEL:-peak_4x}"
SEED="${SEED:-1}"
NUM_REQS="${NUM_REQS:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-3}"

cd "$ROOT"
mkdir -p "$EXP/logs" "$EXP/results"

run_one() {
  local arm_num="$1" arm_name="$2" policy="$3" cluster="$4" dataset="$5"
  shift 5
  local extra_flags=("$@")
  local run_name="${LOAD_LEVEL}_seed${SEED}_${arm_num}_${arm_name}"
  local outdir="$EXP/results/${run_name}"
  mkdir -p "$outdir"

  echo "[start] $run_name"
  python3 -m serving \
    --cluster-config "$EXP/configs/${cluster}" \
    --dataset "$EXP/workloads/${dataset}" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/${run_name}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$EXP/placements/unbal_users.csv" \
    --geographic-gpus-csv "$EXP/placements/unbal_gpus.csv" \
    --run-id "unbal-probe-${LOAD_LEVEL}-seed${SEED}-${arm_num}_${arm_name}" \
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
JOBS+=("1|no_redirect|NEAREST_KV|rtx4090_unbal.json|unbal_${LOAD_LEVEL}_seed${SEED}.jsonl|")
JOBS+=("2|redirect_no_kv|NEAREST_MIGRATE|rtx4090_unbal.json|unbal_${LOAD_LEVEL}_seed${SEED}.jsonl|")
JOBS+=("3|redirect_kv_nopp|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_unbal.json|unbal_${LOAD_LEVEL}_seed${SEED}.jsonl|")
JOBS+=("4|redirect_kv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_unbal_pp2.json|unbal_${LOAD_LEVEL}_seed${SEED}_pp2.jsonl|--pp-size 2")
JOBS+=("5|redirect_nokv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_unbal_pp2.json|unbal_${LOAD_LEVEL}_seed${SEED}_pp2.jsonl|--pp-size 2 --disable-kv-carry")
JOBS+=("6|redirect_kv_pp2_c|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_unbal_pp2.json|unbal_${LOAD_LEVEL}_seed${SEED}_pp2.jsonl|--pp-size 2 --enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3")

running=0
for job in "${JOBS[@]}"; do
  IFS='|' read -r arm_num arm_name policy cluster dataset extra <<< "$job"
  # shellcheck disable=SC2206
  extra_flags=($extra)

  run_one "$arm_num" "$arm_name" "$policy" "$cluster" "$dataset" "${extra_flags[@]}" &
  running=$((running + 1))

  if [ "$running" -ge "$MAX_PARALLEL" ]; then
    wait -n
    running=$((running - 1))
  fi
done

wait
echo "=== all ${LOAD_LEVEL} probe jobs finished ==="
