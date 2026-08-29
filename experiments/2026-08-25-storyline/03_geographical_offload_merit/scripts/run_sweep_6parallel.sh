#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/prepare_experiment.py"

printf '%s\n' 10 9 8 7 6 5 | xargs -P 6 -I PEAK \
  bash -c '"$1/run_worker.sh" local_only "$2"' _ "$SCRIPT_DIR" PEAK
printf '%s\n' 10 9 8 7 6 5 | xargs -P 6 -I PEAK \
  bash -c '"$1/run_worker.sh" redirect_cold "$2"' _ "$SCRIPT_DIR" PEAK

