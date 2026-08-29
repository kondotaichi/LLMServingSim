#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_FILE="$(mktemp)"
trap 'rm -f "$JOB_FILE"' EXIT

for peak in 10 9 8 7 6 5 4 3 2 1; do
  for environment in cloud apn; do
    for pp in 1 2; do
      printf '%s %s %s\n' "$environment" "$pp" "$peak" >> "$JOB_FILE"
    done
  done
done

xargs -P 8 -n 3 bash "$SCRIPT_DIR/run_worker.sh" < "$JOB_FILE"
