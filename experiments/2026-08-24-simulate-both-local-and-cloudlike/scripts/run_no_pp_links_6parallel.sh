#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for environment in distributed_apn distributed_normal_wan; do
  for level in 8 9 10; do
    for method in nearest_kv load; do
      printf '%s %s %s\n' "$environment" "$level" "$method"
    done
  done
done | xargs -P 6 -n 3 "$SCRIPT_DIR/run_no_pp_link_worker.sh"
