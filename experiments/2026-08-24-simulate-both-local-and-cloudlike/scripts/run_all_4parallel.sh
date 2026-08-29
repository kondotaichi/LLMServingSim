#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for environment in distributed clustered_cloud; do
  for level in 1 2 3 4 5 6 7 8 9 10; do
    printf '%s %s\n' "$environment" "$level"
  done
done | xargs -P 8 -n 2 "$SCRIPT_DIR/run_worker.sh"
