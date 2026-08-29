#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/prepare_experiment.py"

for peak in 5 4 3 2; do
  printf '%s %s\n' kv_redirect_only "$peak"
  printf '%s %s\n' pp_only "$peak"
  printf '%s %s\n' kv_redirect_pp "$peak"
done | xargs -P 6 -n 2 "$SCRIPT_DIR/run_worker.sh"
