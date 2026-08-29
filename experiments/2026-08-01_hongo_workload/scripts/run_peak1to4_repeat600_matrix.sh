#!/usr/bin/env bash
# Launch four isolated containers, one per workload, for 16 total runs.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
IMAGE="${IMAGE:-llmservingsim-sim:local}"

cd "$ROOT"
python3 "$EXP_ROOT/scripts/build_peak1to4_repeat600.py"

declare -a PIDS=()
for level in 1 2 3 4; do
  echo "[launch container] Peak ${level}x"
  docker run --rm \
    --name "llmservingsim_peak${level}x_repeat600" \
    -v "$ROOT:/app/LLMServingSim" \
    --tmpfs /app/LLMServingSim/astra-sim/tmp__mem \
    -w /app/LLMServingSim \
    "$IMAGE" \
    bash "experiments/2026-08-01_hongo_workload/scripts/run_peak_repeat600_worker.sh" \
    "$level" &
  PIDS+=("$!")
done

failed=0
for index in "${!PIDS[@]}"; do
  if ! wait "${PIDS[$index]}"; then
    echo "[failed workload] Peak $((index + 1))x" >&2
    failed=1
  fi
done
if [ "$failed" -ne 0 ]; then
  exit 1
fi

for level in 1 2 3 4; do
  python3 "$EXP_ROOT/scripts/plot_ttft_breakdown.py" \
    --prefix "peak_${level}x_repeat600_seed1" \
    --results-dir "$EXP_ROOT/results" \
    --analysis-dir "$EXP_ROOT/analysis" \
    --figures-dir "$EXP_ROOT/figures" \
    --min-requests 600 \
    --title "Hongo Peak ${level}x repeat (600 req): four-arm TTFT breakdown" \
    --cdf-title "Hongo Peak ${level}x repeat (600 req): four-arm TTFT CDF"
done

echo "All 16 repeated Peak 1x-4x runs completed successfully."
