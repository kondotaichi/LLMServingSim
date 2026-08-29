#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\n' 1 2 3 4 5 6 7 8 9 10 | xargs -P 4 -I {} "$SCRIPT_DIR/run_cloud_aligned_worker.sh" {}
