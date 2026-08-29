#!/usr/bin/env bash
# Compare normal, recent-count proactive, and perfect-prewarm oracle.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-09-test-some-workload"
RUN_ROOT="$EXP/results/revisit45_300_three_way"
MAX_PARALLEL="${MAX_PARALLEL:-3}"

cd "$ROOT"
mkdir -p "$RUN_ROOT" "$EXP/logs"

run_one() {
  local level="$1" mode="$2"
  local dataset="$EXP/workloads/revisit45_300/hongo_peak_${level}_revisit45_seed1.jsonl"
  local outdir="$RUN_ROOT/peak_${level}/${mode}"
  local runner="python3 -m serving"
  local extra_flags=""
  if [ "$mode" = "recent_count" ]; then
    extra_flags="--enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3 --proactive-kv-prewarm-output $outdir/proactive_migrations.csv"
  elif [ "$mode" = "oracle" ]; then
    runner="python3 $EXP/scripts/run_perfect_prewarm_oracle.py"
  fi

  mkdir -p "$outdir"
  echo "[start] peak_${level} ${mode}"
  # shellcheck disable=SC2086
  $runner \
    --cluster-config "$EXP/configs/rtx4090_hongo.json" \
    --dataset "$dataset" \
    --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
    --num-reqs 300 \
    --max-num-seqs 128 \
    --max-num-batched-tokens 2048 \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/revisit45_peak_${level}_${mode}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
    --run-id "revisit45-peak-${level}-${mode}" \
    --log-level WARNING \
    $extra_flags \
    > "$EXP/logs/revisit45_peak_${level}_${mode}.log" 2>&1
  local rc=$?
  echo "[done rc=$rc] peak_${level} ${mode}"
  return "$rc"
}

running=0
for level in 2x 5x 10x; do
  for mode in normal recent_count oracle; do
    run_one "$level" "$mode" &
    running=$((running + 1))
    if [ "$running" -ge "$MAX_PARALLEL" ]; then
      wait -n
      running=$((running - 1))
    fi
  done
done
wait
echo "=== revisit45 three-way comparison finished ==="
