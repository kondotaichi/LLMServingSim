#!/usr/bin/env bash
# Run the four daily-average methods concurrently in isolated containers.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$EXP_ROOT/../.." && pwd)"
IMAGE="${IMAGE:-llmservingsim-sim:local}"

cd "$ROOT"
python3 "$EXP_ROOT/scripts/build_daily_average_repeat600.py"

declare -a PIDS=()
for arm in 1 2 3 4; do
  echo "[launch container] daily average arm ${arm}"
  docker run --rm \
    --name "llmservingsim_daily_average_repeat600_arm${arm}" \
    -v "$ROOT:/app/LLMServingSim" \
    --tmpfs /app/LLMServingSim/astra-sim/tmp__mem \
    -w /app/LLMServingSim \
    "$IMAGE" \
    bash "experiments/2026-08-01_hongo_workload/scripts/run_daily_average_repeat600_worker.sh" \
    "$arm" &
  PIDS+=("$!")
done

failed=0
for index in "${!PIDS[@]}"; do
  if ! wait "${PIDS[$index]}"; then
    echo "[failed arm] $((index + 1))" >&2
    failed=1
  fi
done
if [ "$failed" -ne 0 ]; then
  exit 1
fi

python3 "$EXP_ROOT/scripts/plot_ttft_breakdown.py" \
  --prefix "daily_average_repeat600_seed1" \
  --results-dir "$EXP_ROOT/results" \
  --analysis-dir "$EXP_ROOT/analysis" \
  --figures-dir "$EXP_ROOT/figures" \
  --min-requests 600 \
  --title "Hongo daily average repeat (600 req): four-arm TTFT breakdown" \
  --cdf-title "Hongo daily average repeat (600 req): four-arm TTFT CDF"

echo "All four repeated daily-average runs completed successfully."
