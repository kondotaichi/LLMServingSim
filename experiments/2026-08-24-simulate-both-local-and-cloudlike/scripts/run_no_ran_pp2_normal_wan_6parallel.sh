#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for level in 8 9 10; do
  for method in nearest_kv load; do
    printf '%s %s\n' "$level" "$method"
  done
done | xargs -P 6 -n 2 "$SCRIPT_DIR/run_no_ran_pp2_normal_wan_worker.sh"
