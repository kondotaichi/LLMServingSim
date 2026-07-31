#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DATASET_DIR="experiments/2026-07-22_pp2_five_workloads/workloads"
cd "${REPO_ROOT}"

# Method A ("scheduler-hide") + Method C ("proactive KV pre-warm") layered
# together on top of the PP1 (ten independent single-GPU replicas) baseline
# -- NOT PP2. This isolates the software-only contribution of A+C, per the
# 3-part narrative:
#   1. PP1 baseline vs PP1 + Method A+C            <- this script
#   2. PP1 vs PP2 alone, no Method A/B/C            <- already measured in
#      experiments/2026-07-22_pp2_five_workloads
#   3. PP2 + Method A+C combined                    <- future work
# See ../reports/verification.md and .claude/plans/elegant-dazzling-lobster.md
# for the full design/rationale. Method C parameters are left at their
# Stage-1 defaults (threshold 0.8, top-K 3, 30s lookback) for comparability;
# sweeping them is a separate, later task.

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
ONLY_WORKLOAD="${ONLY_WORKLOAD:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
SIM_CONTAINER="${SIM_CONTAINER:-llmservingsim_sim_local}"
ARM_NAME="pp1_proactive_prewarm"

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
  echo "Dispatching PP1-proactive-prewarm simulations into ${SIM_CONTAINER}..."
  exec docker exec \
    -e MAX_PARALLEL="${MAX_PARALLEL}" \
    -e SKIP_COMPLETED="${SKIP_COMPLETED}" \
    -e ONLY_WORKLOAD="${ONLY_WORKLOAD}" \
    -e PYTHON_BIN="${PYTHON_BIN}" \
    "${SIM_CONTAINER}" \
    bash -lc "cd /app/LLMServingSim && ./experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_c_proactive_kv_prewarm/run_pp1_proactive_prewarm_parallel.sh"
fi

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 8)); then
  echo "MAX_PARALLEL must be between 1 and 8." >&2
  exit 2
fi

workloads=(
  input8000_reuse00
  input8000_reuse025
  input8000_reuse05
)

mkdir -p "${SCRIPT_DIR}/results/${ARM_NAME}" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

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
  local output_dir="${SCRIPT_DIR}/results/${ARM_NAME}/${workload}"
  local log_file="${SCRIPT_DIR}/logs/${ARM_NAME}_${workload}.log"
  local status_file="${SCRIPT_DIR}/status/${ARM_NAME}_${workload}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --pp-size 1 \
      --dataset "${DATASET_DIR}/${workload}_pp1.jsonl" \
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
      --enable-scheduler-hide-kv-migration \
      --enable-proactive-kv-prewarm \
      --proactive-kv-prewarm-output "${output_dir}/proactive_migration_log.csv" \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --gpu-utilization-timeseries-output "${output_dir}/gpu_utilization_timeseries.csv" \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --run-id "pp1-proactive-prewarm-ten-server-${workload}" \
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
  output_dir="${SCRIPT_DIR}/results/${ARM_NAME}/${workload}"
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
    echo "One or more ${ARM_NAME} simulations failed; inspect ${SCRIPT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All ${ARM_NAME} workload simulations completed."
echo "Results: ${SCRIPT_DIR}/results/${ARM_NAME}"
