#!/usr/bin/env bash
# Run the original Peak 5x workload for twice as long with arms 1-4.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
EXP="experiments/2026-08-01_hongo_workload"
PREFIX="peak_5x_repeat600_seed1"
ARM_FILTER="${ARM_FILTER:-1,2,3,4}"
SKIP_COMPLETED="${SKIP_COMPLETED:-0}"

cd "$ROOT"
python3 "$EXP/scripts/build_peak5_repeat600.py"
mkdir -p "$EXP/results" "$EXP/logs" "$EXP/analysis" "$EXP/figures"

run_one() {
  local number="$1" name="$2" policy="$3" cluster="$4" dataset="$5"
  shift 5
  local output="$EXP/results/${PREFIX}_${number}_${name}"
  mkdir -p "$output"
  echo "[start] ${number}_${name}"
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
  echo "[done rc=$rc] ${number}_${name}"
  return "$rc"
}

declare -a JOBS=(
  "1|no_redirect|NEAREST_KV|rtx4090_hongo.json|hongo_peak_5x_repeat600_seed1.jsonl"
  "2|redirect_no_kv|NEAREST_MIGRATE|rtx4090_hongo.json|hongo_peak_5x_repeat600_seed1.jsonl"
  "3|redirect_kv_nopp|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_hongo.json|hongo_peak_5x_repeat600_seed1.jsonl"
  "4|redirect_kv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|rtx4090_hongo_pp2.json|hongo_peak_5x_repeat600_seed1_pp2.jsonl|--pp-size 2"
)
failed=0
for job in "${JOBS[@]}"; do
  IFS='|' read -r number name policy cluster dataset extra <<< "$job"
  if [[ ",$ARM_FILTER," != *",$number,"* ]]; then
    continue
  fi
  output="$EXP/results/${PREFIX}_${number}_${name}/requests.csv"
  if [ "$SKIP_COMPLETED" = "1" ] && [ -f "$output" ] && [ "$(wc -l < "$output")" -eq 601 ]; then
    echo "[skip completed] ${number}_${name}"
    continue
  fi
  extra_flags=()
  if [ -n "${extra:-}" ]; then
    read -r -a extra_flags <<< "$extra"
  fi
  # ASTRA-Sim still uses a shared tmp__mem path outside inputs_root, so these
  # runs must remain sequential even though their other generated inputs are
  # isolated by run name.
  if ! run_one "$number" "$name" "$policy" "$cluster" "$dataset" "${extra_flags[@]}"; then
    echo "[failed] ${number}_${name}" >&2
    failed=1
  fi
done
if [ "$failed" -ne 0 ]; then
  exit 1
fi

python3 "$EXP/scripts/plot_ttft_breakdown.py" \
  --prefix "$PREFIX" \
  --results-dir "$EXP/results" \
  --analysis-dir "$EXP/analysis" \
  --figures-dir "$EXP/figures" \
  --min-requests 600 \
  --title "Hongo Peak 5x repeat (600 req): four-arm TTFT breakdown" \
  --cdf-title "Hongo Peak 5x repeat (600 req): four-arm TTFT CDF"

echo "All four Peak 5x repeat runs completed successfully."
