#!/usr/bin/env bash
# Run the matched five-arm hotspot comparison concurrently.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-09-test-some-workload"
HONGO_EXP="experiments/2026-08-01_hongo_workload"
RUN_TAG="${RUN_TAG:-five_arm_load_12x_hot50_msq32}"
MAX_PARALLEL="${MAX_PARALLEL:-5}"
ARM_FILTER="${ARM_FILTER:-1,2,3,4,5}"
DATASET_OVERRIDE="${DATASET_OVERRIDE:-}"

cd "$ROOT"
mkdir -p "$EXP/results/$RUN_TAG" "$EXP/logs"

run_one() {
  local arm_num="$1" arm_name="$2" policy="$3" pp="$4" proactive="$5"
  local dataset cluster pp_flag proactive_flags outdir max_num_seqs max_batched_tokens
  if [ "$pp" -eq 2 ]; then
    dataset="$EXP/workloads/pp2/load_12x_hot50.jsonl"
    cluster="$HONGO_EXP/configs/rtx4090_hongo_pp2.json"
    pp_flag="--pp-size 2"
    max_num_seqs="${PP2_MAX_NUM_SEQS:-64}"
    max_batched_tokens="${PP2_MAX_BATCHED_TOKENS:-4096}"
  else
    dataset="$EXP/workloads/pp1/load_12x_hot50.jsonl"
    cluster="$HONGO_EXP/configs/rtx4090_hongo.json"
    pp_flag=""
    max_num_seqs="${PP1_MAX_NUM_SEQS:-32}"
    max_batched_tokens="${PP1_MAX_BATCHED_TOKENS:-2048}"
  fi
  if [ -n "$DATASET_OVERRIDE" ]; then
    dataset="$DATASET_OVERRIDE"
  fi
  proactive_flags=""
  if [ "$proactive" = "1" ]; then
    proactive_flags="--enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3 --proactive-kv-prewarm-output $EXP/results/$RUN_TAG/${arm_num}_${arm_name}/proactive_migrations.csv"
  fi
  outdir="$EXP/results/$RUN_TAG/${arm_num}_${arm_name}"
  mkdir -p "$outdir"
  echo "[start] ${arm_num}_${arm_name}"
  # shellcheck disable=SC2086
  python3 -m serving \
    --cluster-config "$cluster" \
    --dataset "$dataset" \
    --request-routing-policy "$policy" \
    --num-reqs 300 \
    --max-num-seqs "$max_num_seqs" \
    --max-num-batched-tokens "$max_batched_tokens" \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/method_fix_${RUN_TAG}_${arm_num}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$HONGO_EXP/placements/hongo_users.csv" \
    --geographic-gpus-csv "$HONGO_EXP/placements/hongo_gpus.csv" \
    --run-id "method-fix-${RUN_TAG}-${arm_num}" \
    --log-level WARNING \
    $pp_flag $proactive_flags \
    > "$EXP/logs/${RUN_TAG}_${arm_num}_${arm_name}.log" 2>&1
  local rc=$?
  echo "[done rc=$rc] ${arm_num}_${arm_name}"
  return "$rc"
}

declare -a JOBS=(
  "1|naive|NEAREST_KV|1|0"
  "2|redirect_cold|NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE|1|0"
  "3|redirect_kv_pp1|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|1|0"
  "4|redirect_kv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|2|0"
  "5|redirect_kv_pp2_proactive|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|2|1"
)

running=0
for job in "${JOBS[@]}"; do
  IFS='|' read -r arm_num arm_name policy pp proactive <<< "$job"
  if [[ ",$ARM_FILTER," != *",$arm_num,"* ]]; then
    continue
  fi
  run_one "$arm_num" "$arm_name" "$policy" "$pp" "$proactive" &
  running=$((running + 1))
  if [ "$running" -ge "$MAX_PARALLEL" ]; then
    wait -n
    running=$((running - 1))
  fi
done
wait
echo "=== five-arm hotspot comparison finished ==="
