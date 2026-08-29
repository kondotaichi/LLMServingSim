#!/usr/bin/env bash
# Run matched PP2 cold/KV comparisons for three deterministic hotspot levels.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"

EXP="experiments/2026-08-09-test-some-workload"
BASE_WORKLOAD="${BASE_WORKLOAD:-$EXP/workloads/full/hongo_peak_5x_600_seed1_pp2.jsonl}"
RUN_TAG="${RUN_TAG:-pp2_kv_hotspot_sweep_peak5_600}"
NUM_REQS="${NUM_REQS:-600}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
MAX_BATCHED_TOKENS="${MAX_BATCHED_TOKENS:-4096}"

cd "$ROOT"
mkdir -p "$EXP/workloads/pp2" "$EXP/results/$RUN_TAG" "$EXP/logs"

declare -a HOTSPOTS=(30 40 50)
declare -a ARMS=(cold kv)
declare -a PIDS=()
declare -a JOB_NAMES=()

for hotspot in "${HOTSPOTS[@]}"; do
  workload="$EXP/workloads/pp2/validation_peak5_600_hot${hotspot}.jsonl"
  python3 "$EXP/scripts/make_spatial_skew.py" \
    --input "$BASE_WORKLOAD" \
    --output "$workload" \
    --hot-instance 0 \
    --hot-share "0.${hotspot}" \
    --num-instances 6
done

run_one() {
  local hotspot="$1"
  local arm="$2"
  local policy
  local workload="$EXP/workloads/pp2/validation_peak5_600_hot${hotspot}.jsonl"
  local outdir="$EXP/results/$RUN_TAG/hot${hotspot}/pp2_${arm}"
  local logfile="$EXP/logs/${RUN_TAG}_hot${hotspot}_pp2_${arm}.log"

  if [ "$arm" = "cold" ]; then
    policy="NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE"
  else
    policy="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
  fi

  mkdir -p "$outdir"
  echo "[start] hot${hotspot} pp2_${arm}"
  python3 -m serving \
    --cluster-config "$EXP/configs/rtx4090_hongo_pp2.json" \
    --dataset "$workload" \
    --request-routing-policy "$policy" \
    --num-reqs "$NUM_REQS" \
    --pp-size 2 \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --max-num-batched-tokens "$MAX_BATCHED_TOKENS" \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --inputs-root "/tmp/astra_runs/${RUN_TAG}_hot${hotspot}_pp2_${arm}" \
    --output "$outdir/requests.csv" \
    --geographic-user-output "$outdir/users.csv" \
    --geographic-gpu-output "$outdir/gpus.csv" \
    --geographic-metadata-output "$outdir/metadata.json" \
    --geographic-users-csv "$EXP/placements/hongo_users.csv" \
    --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
    --run-id "${RUN_TAG}-hot${hotspot}-pp2-${arm}" \
    --log-level WARNING \
    > "$logfile" 2>&1
  local rc=$?
  echo "[done rc=$rc] hot${hotspot} pp2_${arm}"
  return "$rc"
}

for hotspot in "${HOTSPOTS[@]}"; do
  for arm in "${ARMS[@]}"; do
    run_one "$hotspot" "$arm" &
    PIDS+=("$!")
    JOB_NAMES+=("hot${hotspot}_pp2_${arm}")
  done
done

failed=0
for index in "${!PIDS[@]}"; do
  if ! wait "${PIDS[$index]}"; then
    echo "[failed] ${JOB_NAMES[$index]}" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "One or more hotspot sweep jobs failed. Check $EXP/logs/${RUN_TAG}_*.log" >&2
  exit 1
fi

echo "All six PP2 hotspot sweep jobs completed successfully."
echo "Results: $EXP/results/$RUN_TAG"
