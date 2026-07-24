#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

MAX_PARALLEL="${MAX_PARALLEL:-5}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
ONLY_WORKLOAD="${ONLY_WORKLOAD:-}"
SKIP_PREPARE="${SKIP_PREPARE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
SIM_CONTAINER="${SIM_CONTAINER:-llmservingsim_sim_local}"

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
  echo "Dispatching five PP1 baseline simulations into ${SIM_CONTAINER}..."
  exec docker exec \
    -e MAX_PARALLEL="${MAX_PARALLEL}" \
    -e SKIP_COMPLETED="${SKIP_COMPLETED}" \
    -e ONLY_WORKLOAD="${ONLY_WORKLOAD}" \
    -e SKIP_PREPARE="${SKIP_PREPARE}" \
    -e PYTHON_BIN="${PYTHON_BIN}" \
    "${SIM_CONTAINER}" \
    bash -lc "cd /app/LLMServingSim && ./experiments/2026-07-22_pp2_five_workloads/run_pp1_parallel.sh"
fi

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 5)); then
  echo "MAX_PARALLEL must be between 1 and 5." >&2
  exit 2
fi

workload_names=(
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

workload_paths=(
  experiments/2026-07-14_input_reuse_90s_sweep/workloads/input512_reuse00.jsonl
  experiments/2026-07-14_input_reuse_90s_sweep/workloads/input2000_reuse025.jsonl
  workloads/generated/cell_apn/prompt6000_90s/sharegpt_300_prompt6000_reuse50_90s.jsonl
  experiments/2026-07-22_pp2_five_workloads/workloads/input8000_reuse00_pp1.jsonl
  experiments/2026-07-22_pp2_five_workloads/workloads/input8000_reuse025_pp1.jsonl
  experiments/2026-07-22_pp2_five_workloads/workloads/input8000_reuse05_pp1.jsonl
  experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse00.jsonl
  experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse025.jsonl
  experiments/2026-07-14_input_reuse_90s_sweep/workloads/input10000_reuse05.jsonl
  experiments/2026-07-21_mixed_workload_model_value/workloads/mixed_rate3p33_seed1.jsonl
)

mkdir -p "${SCRIPT_DIR}/results/pp1" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"
if [[ "${SKIP_PREPARE}" != "1" ]]; then
  "${PYTHON_BIN}" "${SCRIPT_DIR}/scripts/prepare_workloads.py"
fi

is_complete() {
  local output_dir="$1"
  [[ -s "${output_dir}/requests.csv" ]] && \
    [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]] && \
    [[ -s "${output_dir}/gpus.csv" ]] && \
    head -n 1 "${output_dir}/gpus.csv" | grep -q 'utilization_pct' && \
    [[ -s "${output_dir}/gpu_utilization_timeseries.csv" ]]
}

run_workload() {
  local workload_name="$1"
  local workload_path="$2"
  local output_dir="${SCRIPT_DIR}/results/pp1/${workload_name}"
  local log_file="${SCRIPT_DIR}/logs/pp1_${workload_name}.log"
  local status_file="${SCRIPT_DIR}/status/pp1_${workload_name}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
      --pp-size 1 \
      --dataset "${workload_path}" \
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
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --gpu-utilization-timeseries-output "${output_dir}/gpu_utilization_timeseries.csv" \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --run-id "pp1-ten-server-${workload_name}" \
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

task_indices=()
for index in "${!workload_names[@]}"; do
  if [[ -n "${ONLY_WORKLOAD}" && "${workload_names[index]}" != "${ONLY_WORKLOAD}"* ]]; then
    continue
  fi
  output_dir="${SCRIPT_DIR}/results/pp1/${workload_names[index]}"
  if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${output_dir}"; then
    echo "Skipping completed PP1 ${workload_names[index]}"
  else
    task_indices+=("${index}")
  fi
done

echo "Prepared ${#task_indices[@]} PP1 simulations with max parallelism ${MAX_PARALLEL}."
for ((start = 0; start < ${#task_indices[@]}; start += MAX_PARALLEL)); do
  pids=()
  labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#task_indices[@]}; offset++)); do
    index="${task_indices[start + offset]}"
    echo "Starting PP1 ${workload_names[index]}"
    run_workload "${workload_names[index]}" "${workload_paths[index]}" &
    pids+=("$!")
    labels+=("${workload_names[index]}")
  done

  failed=0
  for index in "${!pids[@]}"; do
    if wait "${pids[index]}"; then
      echo "Completed PP1 ${labels[index]}"
    else
      echo "Failed PP1 ${labels[index]}" >&2
      failed=1
    fi
  done
  if ((failed)); then
    echo "One or more PP1 simulations failed; inspect ${SCRIPT_DIR}/logs." >&2
    exit 1
  fi
done

echo "All PP1 baseline simulations completed."
echo "Results: ${SCRIPT_DIR}/results/pp1"
