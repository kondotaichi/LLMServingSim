#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/config.env}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Missing config: ${CONFIG_FILE}" >&2
  echo "Copy config.env.example to config.env and fill in the Miyabi values." >&2
  exit 1
fi

set -a
source "${CONFIG_FILE}"
set +a

required_vars=(
  MIYABI_ENDPOINT
  MIYABI_MODEL
  MIYABI_GPU_MODEL
  MIYABI_NODE_COUNT
  MIYABI_GPUS_PER_NODE
  MIYABI_REPLICA_COUNT
  MIYABI_TENSOR_PARALLEL_SIZE
  MIYABI_ROUTER_POLICY
  MIYABI_VLLM_VERSION
  MIYABI_CONTAINER_DIGEST
  MIYABI_MODEL_REVISION
)
for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" || "${!name}" == "REPLACE_ME" ]]; then
    echo "Set ${name} in ${CONFIG_FILE}" >&2
    exit 1
  fi
done

load="${1:-}"
mode="${2:-full}"
if [[ ! "${load}" =~ ^([1-9]|10)$ ]]; then
  echo "Usage: ./run_replay.sh <load: 1-10> [smoke|full]" >&2
  exit 1
fi
if [[ "${mode}" != "smoke" && "${mode}" != "full" ]]; then
  echo "Mode must be smoke or full" >&2
  exit 1
fi

azure_dir="${REPO_ROOT}/experiments/2026-08-17_azure_selfhost_vs_local"
workload="${azure_dir}/workloads/hongo_repeat600/hongo_peak_${load}x_repeat600_seed1.jsonl"
manifest="${azure_dir}/workloads/manifests/hongo_peak_1to10_repeat600.csv"
runner="${azure_dir}/scripts/replay_remote_vllm.py"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_id="miyabi-peak-${load}x-repeat600-${mode}-${timestamp}"

args=(
  "${runner}"
  --workload "${workload}"
  --manifest "${manifest}"
  --endpoint "${MIYABI_ENDPOINT}"
  --model "${MIYABI_MODEL}"
  --output-dir "${SCRIPT_DIR}/results"
  --run-id "${run_id}"
  --api-key-env MIYABI_API_KEY
  --max-client-concurrency "${MAX_CLIENT_CONCURRENCY:-512}"
  --connect-timeout "${CONNECT_TIMEOUT_S:-10}"
  --first-token-timeout "${FIRST_TOKEN_TIMEOUT_S:-120}"
  --request-timeout "${REQUEST_TIMEOUT_S:-1800}"
  --warmup-requests "${WARMUP_REQUESTS:-1}"
  --failure-retries "${FAILURE_RETRIES:-1}"
  --launch-lag-p99-threshold-ms "${LAUNCH_LAG_P99_THRESHOLD_MS:-100}"
  --server-gpu-model "${MIYABI_GPU_MODEL}"
  --server-gpu-count "$((MIYABI_NODE_COUNT * MIYABI_GPUS_PER_NODE))"
  --server-vm-sku "Miyabi ${MIYABI_NODE_COUNT}-node allocation"
  --server-replica-count "${MIYABI_REPLICA_COUNT}"
  --tensor-parallel-size "${MIYABI_TENSOR_PARALLEL_SIZE}"
  --router-policy "${MIYABI_ROUTER_POLICY}"
  --server-environment miyabi
  --server-vllm-version "${MIYABI_VLLM_VERSION}"
  --server-container-digest "${MIYABI_CONTAINER_DIGEST}"
  --server-model-revision "${MIYABI_MODEL_REVISION}"
)
if [[ "${mode}" == "smoke" ]]; then
  args+=(--max-requests 10)
fi

cd "${REPO_ROOT}"
python3 "${args[@]}"
