#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/prepare_experiment.py"

for method in local_only redirect_cold kv_redirect pp_only proposed; do
  printf '%s %s\n' "$method" 5
done | xargs -P 5 -n 2 "$SCRIPT_DIR/run_worker.sh"

python3 "$SCRIPT_DIR/analyze_peak5.py"
