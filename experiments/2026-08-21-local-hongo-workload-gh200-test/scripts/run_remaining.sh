#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MAX_PARALLEL_RUNS="${MAX_PARALLEL_RUNS:-4}"

case "$MAX_PARALLEL_RUNS" in
  ''|*[!0-9]*|0) echo "MAX_PARALLEL_RUNS must be a positive integer" >&2; exit 2 ;;
esac

method_name() {
  case "$1" in
    1) echo naive ;;
    2) echo redirect_no_kv ;;
    3) echo redirect_kv_no_pp ;;
    4) echo redirect_kv_pp2 ;;
  esac
}

is_complete() {
  local level="$1" method="$2" name output rows
  name="$(method_name "$method")"
  output="$EXP_ROOT/results/peak_${level}x_repeat600_seed1_${method}_${name}/requests.csv"
  [ -f "$output" ] || return 1
  rows="$(awk 'END {print NR - 1}' "$output")"
  [ "$rows" -eq 600 ]
}

failed=0
declare -a batch_pids=()
declare -a batch_labels=()

for level in $(seq 1 10); do
  for method in 1 2 3 4; do
    if is_complete "$level" "$method"; then
      continue
    fi
    label="peak_${level}x_method_${method}_$(method_name "$method")"
    echo "[queue] $label"
    METHOD_FILTER="$method" "$SCRIPT_DIR/run_peak_worker.sh" "$level" &
    batch_pids+=("$!")
    batch_labels+=("$label")
    if [ "${#batch_pids[@]}" -ge "$MAX_PARALLEL_RUNS" ]; then
      for index in "${!batch_pids[@]}"; do
        if ! wait "${batch_pids[$index]}"; then
          echo "[failed] ${batch_labels[$index]}" >&2
          failed=1
        fi
      done
      batch_pids=()
      batch_labels=()
    fi
  done
done

for index in "${!batch_pids[@]}"; do
  if ! wait "${batch_pids[$index]}"; then
    echo "[failed] ${batch_labels[$index]}" >&2
    failed=1
  fi
done

exit "$failed"
