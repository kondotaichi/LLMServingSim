#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKS="$(mktemp)"
trap 'rm -f "$TASKS"' EXIT

for mode in pp2 no_pp; do
  for level in 10 9 8 7 6 5 4 3 2 1; do
    for environment in clustered_cloud distributed_apn distributed_normal_wan; do
      for method in nearest_kv load; do
        printf '%s %s %s %s\n' "$mode" "$environment" "$level" "$method"
      done
    done
  done
done > "$TASKS"

run_task() {
  local mode="$1" environment="$2" level="$3" method="$4"
  if [ "$mode" = "no_pp" ]; then
    "$SCRIPT_DIR/run_no_pp_link_worker.sh" "$environment" "$level" "$method"
  else
    "$SCRIPT_DIR/run_no_ran_pp2_normal_wan_worker.sh" "$environment" "$level" "$method"
  fi
}
export -f run_task
export SCRIPT_DIR
xargs -P 8 -n 4 bash -c 'run_task "$@"' _ < "$TASKS"
