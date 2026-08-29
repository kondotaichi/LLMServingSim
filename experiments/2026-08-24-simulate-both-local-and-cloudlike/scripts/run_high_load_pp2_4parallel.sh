#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for environment in clustered_cloud distributed_apn distributed_normal_wan; do
  for method in nearest_kv load; do
    printf '%s 7 %s\n' "$environment" "$method"
  done
done | xargs -P 4 -n 3 "$SCRIPT_DIR/run_no_ran_pp2_normal_wan_worker.sh"
