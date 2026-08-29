#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_PARALLEL_LEVELS="${MAX_PARALLEL_LEVELS:-2}"

case "$MAX_PARALLEL_LEVELS" in
  ''|*[!0-9]*|0) echo "MAX_PARALLEL_LEVELS must be a positive integer" >&2; exit 2 ;;
esac

failed=0
declare -a batch_pids=()
declare -a batch_levels=()
for level in 6 7 8 9 10; do
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

exit "$failed"
