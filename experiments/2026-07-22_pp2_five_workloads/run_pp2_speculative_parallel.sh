#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXPERIMENT_REL="experiments/2026-07-22_pp2_five_workloads"
# Raw simulation output (results/logs/status) defaults to this script's
# own directory, matching the original behavior. Override to write
# elsewhere (e.g. the write-up directory these two arms' results were
# relocated to) without moving the workload/topology inputs.
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}}"
cd "${REPO_ROOT}"

# Method B ("speculative + scheduler-hide"): pins the current best-scored
# candidate and speculatively starts its KV transfer while the redirect
# decision is still pending, on top of Method A's scheduler-hide overlap.
# Unlike Method A, this requires THREE simultaneous changes relative to the
# PP1/PP2 baseline: (1) the routing policy switches from the pressure
# heuristic to NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE, because the
# pressure heuristic's `no_model` branch never waits at all (deadline_exceeded
# is unconditionally true); (2) --enable-formula-local-wait-point-estimate
# (B-1), because the default route_upper_ms-based local-wait estimate
# structurally always exceeds --oneshot-max-local-wait-ns too, so redirect
# decisions would still be instantaneous even under the formula policy;
# (3) --enable-scheduler-hide-kv-migration + --enable-speculative-kv-migration.
# See .claude/plans/elegant-dazzling-lobster.md for the full design and
# Stage 2/3 verification steps to run before the full 10-workload sweep here.

MAX_PARALLEL="${MAX_PARALLEL:-5}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
ONLY_WORKLOAD="${ONLY_WORKLOAD:-}"
SKIP_PREPARE="${SKIP_PREPARE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POLICY="NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"
SIM_CONTAINER="${SIM_CONTAINER:-llmservingsim_sim_local}"
ARM_NAME="pp2_spec_scheduler_hide"
# Exposed for Stage 2/3 tuning (see plan): if the genuine-wait rate under
# the recalibrated local-wait estimate looks pathological (near 0 or near
# 100%), adjust these without editing the script.
ONESHOT_REDIRECT_MARGIN_NS="${ONESHOT_REDIRECT_MARGIN_NS:-200000000}"
ONESHOT_MAX_LOCAL_WAIT_NS="${ONESHOT_MAX_LOCAL_WAIT_NS:-1000000000}"

if [[ "$(uname -s)" != "Linux" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to run the Linux ASTRA-Sim binary on this host." >&2
    echo "Install/start Docker Desktop, then run ./scripts/docker-sim.sh once." >&2
    exit 2
  fi
  if ! docker container inspect "${SIM_CONTAINER}" >/dev/null 2>&1; then
    echo "Simulator container '${SIM_CONTAINER}' does not exist." >&2
    echo "Create it first with: ./scripts/docker-sim.sh" >&2
    echo "Then exit the container shell and rerun this script on the host." >&2
    exit 2
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "${SIM_CONTAINER}")" != "true" ]]; then
    echo "Starting simulator container ${SIM_CONTAINER}..."
    docker start "${SIM_CONTAINER}" >/dev/null
  fi
  echo "Dispatching five PP2-speculative simulations into ${SIM_CONTAINER}..."
  exec docker exec \
    -e MAX_PARALLEL="${MAX_PARALLEL}" \
    -e OUTPUT_DIR="/app/LLMServingSim/${OUTPUT_DIR#${REPO_ROOT}/}" \
    -e SKIP_COMPLETED="${SKIP_COMPLETED}" \
    -e ONLY_WORKLOAD="${ONLY_WORKLOAD}" \
    -e SKIP_PREPARE="${SKIP_PREPARE}" \
    -e PYTHON_BIN="${PYTHON_BIN}" \
    -e ONESHOT_REDIRECT_MARGIN_NS="${ONESHOT_REDIRECT_MARGIN_NS}" \
    -e ONESHOT_MAX_LOCAL_WAIT_NS="${ONESHOT_MAX_LOCAL_WAIT_NS}" \
    "${SIM_CONTAINER}" \
    bash -lc "cd /app/LLMServingSim && ./${EXPERIMENT_REL}/run_pp2_speculative_parallel.sh"
fi

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 5)); then
  echo "MAX_PARALLEL must be between 1 and 5." >&2
  exit 2
fi

workloads=(
  input512_reuse00
  input2000_reuse025
  input6000_reuse05
  input8000_reuse00
  input8000_reuse025
  input8000_reuse05
  input10000_reuse00
  input10000_reuse025
  input10000_reuse05
  mixed_rate3p33_seed1
)

if [[ "${SKIP_PREPARE}" != "1" ]]; then
  "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/prepare_workloads.py"
fi
mkdir -p "${OUTPUT_DIR}/results/${ARM_NAME}" "${OUTPUT_DIR}/logs" "${OUTPUT_DIR}/status"

is_complete() {
  local output_dir="$1"
  [[ -s "${output_dir}/requests.csv" ]] && \
    [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]] && \
    [[ -s "${output_dir}/gpus.csv" ]] && \
    head -n 1 "${output_dir}/gpus.csv" | grep -q 'utilization_pct' && \
    [[ -s "${output_dir}/gpu_utilization_timeseries.csv" ]]
}

run_workload() {
  local workload="$1"
  local output_dir="${OUTPUT_DIR}/results/${ARM_NAME}/${workload}"
  local log_file="${OUTPUT_DIR}/logs/${ARM_NAME}_${workload}.log"
  local status_file="${OUTPUT_DIR}/status/${ARM_NAME}_${workload}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/five_node_rtx4090_apn.json \
      --pp-size 2 \
      --dataset "${EXPERIMENT_REL}/workloads/${workload}.jsonl" \
      --num-reqs 300 \
      --request-routing-policy "${POLICY}" \
      --dtype bfloat16 \
      --kv-cache-dtype auto \
      --max-num-seqs 128 \
      --max-num-batched-tokens 2048 \
      --enable-chunked-prefill \
      --enable-prefix-caching \
      --gpu-backbone-bandwidth-gbps 10.7 \
      --apn-fixed-propagation-ns 300500 \
      --kv-staging-bandwidth-gbytes-per-s 33.8 \
      --kv-staging-latency-ns 102.9 \
      --oneshot-redirect-margin-ns "${ONESHOT_REDIRECT_MARGIN_NS}" \
      --oneshot-max-local-wait-ns "${ONESHOT_MAX_LOCAL_WAIT_NS}" \
      --enable-scheduler-hide-kv-migration \
      --enable-formula-local-wait-point-estimate \
      --enable-speculative-kv-migration \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --gpu-utilization-timeseries-output "${output_dir}/gpu_utilization_timeseries.csv" \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --run-id "pp2-spec-scheduler-hide-five-server-v2-${workload}" \
      --log-level WARNING >"${log_file}" 2>&1; then
    if is_complete "${output_dir}"; then
      echo "COMPLETED" >"${status_file}"
      return 0
    fi
    echo "INVALID_OUTPUT" >"${status_file}"
    return 1
  fi

  echo "FAILED" >"${status_file}"
  return 1
}

tasks=()
for workload in "${workloads[@]}"; do
  if [[ -n "${ONLY_WORKLOAD}" && "${workload}" != "${ONLY_WORKLOAD}"* ]]; then
    continue
  fi
  output_dir="${OUTPUT_DIR}/results/${ARM_NAME}/${workload}"
  if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${output_dir}"; then
    echo "Skipping completed ${ARM_NAME} ${workload}"
  else
    tasks+=("${workload}")
  fi
done

echo "Prepared ${#tasks[@]} ${ARM_NAME} simulations with max parallelism ${MAX_PARALLEL}."
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    workload="${tasks[start + offset]}"
    echo "Starting ${ARM_NAME} ${workload}"
    run_workload "${workload}" &
    pids+=("$!")
    labels+=("${workload}")
  done

  failed=0
  for index in "${!pids[@]}"; do
    if wait "${pids[index]}"; then
      echo "Completed ${ARM_NAME} ${labels[index]}"
    else
      echo "Failed ${ARM_NAME} ${labels[index]}" >&2
      failed=1
    fi
  done
  if ((failed)); then
    echo "One or more ${ARM_NAME} simulations failed; inspect ${OUTPUT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All ${ARM_NAME} workload simulations completed."
echo "Results: ${OUTPUT_DIR}/results/${ARM_NAME}"
