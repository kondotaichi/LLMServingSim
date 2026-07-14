#!/bin/bash

# Launch the simulator Docker container (ASTRA-Sim + sim Python deps).
#
# Mounts the repo root regardless of where this script is invoked from.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../scripts
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"                    # .../LLMServingSim
IMAGE_NAME="llmservingsim-sim:local"
CONTAINER_NAME="llmservingsim_sim_local"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  exec docker start -ai "$CONTAINER_NAME"
fi

if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  docker build \
    -f "$SCRIPT_DIR/Dockerfile.sim" \
    -t "$IMAGE_NAME" \
    "$REPO_ROOT"
fi

docker run --name "$CONTAINER_NAME" \
  -it \
  -v "$REPO_ROOT":/app/LLMServingSim \
  -w /app/LLMServingSim \
  "$IMAGE_NAME" \
  bash
