#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/prepare_experiment.py"

for peak in 4 3 2; do
  printf '%s %s\n' local_only "$peak"
  printf '%s %s\n' redirect_cold "$peak"
done | xargs -P 6 -n 2 "$SCRIPT_DIR/run_worker.sh"
