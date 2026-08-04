#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/../../" && pwd)"

SEED="${SEED:-1}"
NUM_REQS="${NUM_REQS:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
CONTENT_SOURCE="${CONTENT_SOURCE:-$EXP_ROOT/workloads/hongo_peak_2x_seed1.jsonl}"
LEVELS="${LEVELS:-1x 2x 3x 4x 6x 7x 8x 9x 10x}"
PREPARE_WORKLOADS="${PREPARE_WORKLOADS:-auto}"

map_level_to_load() {
  case "$1" in
    1x) echo "busy_hour" ;;
    2x) echo "peak_2x" ;;
    3x) echo "peak_3x" ;;
    4x) echo "peak_4x" ;;
    5x) echo "peak_5x" ;;
    6x) echo "peak_6x" ;;
    7x) echo "peak_7x" ;;
    8x) echo "peak_8x" ;;
    9x) echo "peak_9x" ;;
    10x) echo "peak_10x" ;;
    *)
      echo "unsupported level: $1" >&2
      return 1
      ;;
  esac
}

build_multiplier_args() {
  local -n out_ref=$1
  local level load multiplier
  for level in $LEVELS; do
    load="$(map_level_to_load "$level")"
    if [[ "$load" =~ ^peak_([0-9]+)x$ ]]; then
      multiplier="${BASH_REMATCH[1]}"
      if (( multiplier >= 3 )); then
        out_ref+=("$multiplier")
      fi
    fi
  done
}

ensure_pp2_workload() {
  local load_level="$1"
  local base_path="$EXP_ROOT/workloads/hongo_${load_level}_seed${SEED}.jsonl"
  local pp2_path="$EXP_ROOT/workloads/hongo_${load_level}_seed${SEED}_pp2.jsonl"
  if [[ -f "$pp2_path" ]]; then
    return 0
  fi
  if [[ ! -f "$base_path" ]]; then
    return 1
  fi
  echo "=== generating missing pp2 workload: $(basename "$pp2_path") ==="
  python3 - <<PY
import importlib.util
import json
from pathlib import Path

module_path = Path("$EXP_ROOT/scripts/prepare_hongo_workload.py")
spec = importlib.util.spec_from_file_location("prepare_hongo_workload", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

base_path = Path("$base_path")
pp2_path = Path("$pp2_path")
rows = []
with base_path.open() as handle:
    for line in handle:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

positions = module.gpu_positions()
rows_pp2 = module.transform_pp2_rows(rows, positions)
module.write_jsonl(pp2_path, rows_pp2)
print(f"wrote {pp2_path}")
PY
}

prepare_workloads() {
  local load_level
  local required=()
  local multiplier_args=()
  local multiplier
  local deduped=()
  local path
  local all_present=1
  local base_path
  local pp2_path

  if [[ "$PREPARE_WORKLOADS" == "0" || "$PREPARE_WORKLOADS" == "false" ]]; then
    echo "=== skipping workload generation (PREPARE_WORKLOADS=$PREPARE_WORKLOADS) ==="
    return 0
  fi

  for level in $LEVELS; do
    load_level="$(map_level_to_load "$level")"
    base_path="$EXP_ROOT/workloads/hongo_${load_level}_seed${SEED}.jsonl"
    pp2_path="$EXP_ROOT/workloads/hongo_${load_level}_seed${SEED}_pp2.jsonl"
    if [[ -f "$base_path" && ! -f "$pp2_path" ]]; then
      ensure_pp2_workload "$load_level"
    fi
    required+=(
      "$base_path"
      "$pp2_path"
    )
  done

  if [[ "$PREPARE_WORKLOADS" == "auto" ]]; then
    for path in "${required[@]}"; do
      if [[ ! -f "$path" ]]; then
        all_present=0
        break
      fi
    done
    if (( all_present )); then
      echo "=== skipping workload generation (all requested workload files already exist) ==="
      return 0
    fi
  fi

  build_multiplier_args multiplier_args
  if ((${#multiplier_args[@]} > 0)); then
    while IFS= read -r multiplier; do
      deduped+=("$multiplier")
    done < <(printf '%s\n' "${multiplier_args[@]}" | sort -n | uniq)
  fi
  cd "$REPO_ROOT"
  echo "=== generating workloads for levels: $LEVELS ==="
  if ((${#deduped[@]} > 0)); then
    python3 "$EXP_ROOT/scripts/prepare_hongo_workload.py" \
      --content "$CONTENT_SOURCE" \
      --num-reqs "$NUM_REQS" \
      --derived-peak-multipliers "${deduped[@]}"
  else
    python3 "$EXP_ROOT/scripts/prepare_hongo_workload.py" \
      --content "$CONTENT_SOURCE" \
      --num-reqs "$NUM_REQS"
  fi
}

run_level() {
  local level="$1"
  local load_level
  local prefix
  load_level="$(map_level_to_load "$level")"
  prefix="${load_level}_seed${SEED}"

  echo "=== running ${level} (${load_level}) ==="
  (
    cd "$EXP_ROOT"
    LOAD_LEVEL="$load_level" \
    SEED="$SEED" \
    NUM_REQS="$NUM_REQS" \
    MAX_PARALLEL="$MAX_PARALLEL" \
    bash scripts/run_hongo_probe_5arm.sh

    python3 scripts/plot_ttft_breakdown.py \
      --prefix "$prefix" \
      --results-dir results \
      --analysis-dir analysis \
      --figures-dir figures \
      --min-requests "$NUM_REQS" \
      --title "Hongo ${level} (${NUM_REQS} req, seed${SEED}): five-arm TTFT breakdown" \
      --cdf-title "Hongo ${level} (${NUM_REQS} req, seed${SEED}): TTFT CDF"
  )
}

main() {
  prepare_workloads
  local level
  for level in $LEVELS; do
    run_level "$level"
  done
  echo "=== completed levels: $LEVELS ==="
}

main "$@"
