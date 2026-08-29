#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAN_PROFILE=active20 PEAK_LEVEL=1 exec bash "$SCRIPT_DIR/run_peak1.sh"
