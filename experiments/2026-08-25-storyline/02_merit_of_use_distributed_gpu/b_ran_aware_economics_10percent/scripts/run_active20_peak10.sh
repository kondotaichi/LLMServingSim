#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAN_PROFILE=active20 PEAK_LEVEL=10 exec bash "$SCRIPT_DIR/run_peak1.sh"
