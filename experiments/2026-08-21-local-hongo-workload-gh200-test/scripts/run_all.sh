#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MAX_PARALLEL_LEVELS="${MAX_PARALLEL_LEVELS:-1}"

case "$MAX_PARALLEL_LEVELS" in
  ''|*[!0-9]*|0) echo "MAX_PARALLEL_LEVELS must be a positive integer" >&2; exit 2 ;;
esac

python3 "$SCRIPT_DIR/prepare_experiment.py"

failed=0
declare -a batch_pids=()
declare -a batch_levels=()
for level in $(seq 1 10); do
  "$SCRIPT_DIR/run_peak_worker.sh" "$level" &
  batch_pids+=("$!")
  batch_levels+=("$level")
  if [ "${#batch_pids[@]}" -ge "$MAX_PARALLEL_LEVELS" ]; then
    for index in "${!batch_pids[@]}"; do
      if ! wait "${batch_pids[$index]}"; then
        echo "Peak ${batch_levels[$index]}x failed" >&2
        failed=1
      fi
    done
    batch_pids=()
    batch_levels=()
  fi
done

for index in "${!batch_pids[@]}"; do
  if ! wait "${batch_pids[$index]}"; then
    echo "Peak ${batch_levels[$index]}x failed" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "One or more GH200 simulations failed; inspect $EXP_ROOT/logs" >&2
  exit 1
fi

echo "All 40 GH200 simulations completed successfully."
