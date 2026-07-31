#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DATASET_DIR="experiments/2026-07-22_pp2_five_workloads/workloads"
cd "${REPO_ROOT}"

# Stage 2: sweep --proactive-kv-prewarm-pressure-threshold x
# --proactive-kv-prewarm-top-k on PP2 input8000_reuse025 (Method A+C, same
# base flags as Stage 1) to find a setting that nets positive vs the "A
# only" baseline (959.0ms / 35 redirects, results/pp2_scheduler_hide/).
# See ../reports/verification.md and .claude/plans/elegant-dazzling-lobster.md.

MAX_PARALLEL="${MAX_PARALLEL:-6}"
SKIP_COMPLETED="${SKIP_COMPLETED:-1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
SIM_CONTAINER="${SIM_CONTAINER:-llmservingsim_sim_local}"
WORKLOAD="input8000_reuse025"
THRESHOLDS=(0.6 0.7 0.8 0.9)
TOP_KS=(1 3 5)

if [[ "$(uname -s)" != "Linux" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to run the Linux ASTRA-Sim binary on this host." >&2
    exit 2
  fi
  if ! docker container inspect "${SIM_CONTAINER}" >/dev/null 2>&1; then
    echo "Simulator container '${SIM_CONTAINER}' does not exist." >&2
    exit 2
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "${SIM_CONTAINER}")" != "true" ]]; then
    echo "Starting simulator container ${SIM_CONTAINER}..."
    docker start "${SIM_CONTAINER}" >/dev/null
  fi
  echo "Dispatching Stage 2 sweep into ${SIM_CONTAINER}..."
  exec docker exec \
    -e MAX_PARALLEL="${MAX_PARALLEL}" \
    -e SKIP_COMPLETED="${SKIP_COMPLETED}" \
    -e PYTHON_BIN="${PYTHON_BIN}" \
    "${SIM_CONTAINER}" \
    bash -lc "cd /app/LLMServingSim && ./experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_c_proactive_kv_prewarm/run_stage2_sweep.sh"
fi

if ((MAX_PARALLEL < 1 || MAX_PARALLEL > 8)); then
  echo "MAX_PARALLEL must be between 1 and 8." >&2
  exit 2
fi

mkdir -p "${SCRIPT_DIR}/results/stage2_sweep" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/status"

is_complete() {
  local output_dir="$1"
  [[ -s "${output_dir}/requests.csv" ]] && \
    [[ "$(wc -l <"${output_dir}/requests.csv")" -eq 301 ]]
}

run_combo() {
  local threshold="$1"
  local topk="$2"
  local label="th${threshold}_k${topk}"
  local output_dir="${SCRIPT_DIR}/results/stage2_sweep/${label}"
  local log_file="${SCRIPT_DIR}/logs/stage2_${label}.log"
  local status_file="${SCRIPT_DIR}/status/stage2_${label}.status"
  mkdir -p "${output_dir}"
  echo "RUNNING" >"${status_file}"

  if env TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u -m serving \
      --cluster-config configs/cluster/five_node_rtx4090_apn.json \
      --pp-size 2 \
      --dataset "${DATASET_DIR}/${WORKLOAD}.jsonl" \
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
      --proactive-kv-prewarm-pressure-threshold "${threshold}" \
      --proactive-kv-prewarm-top-k "${topk}" \
      --proactive-kv-prewarm-output "${output_dir}/proactive_migration_log.csv" \
      --graph-converter in-process \
      --trace-io buffered \
      --output "${output_dir}/requests.csv" \
      --geographic-user-output "${output_dir}/users.csv" \
      --geographic-gpu-output "${output_dir}/gpus.csv" \
      --gpu-utilization-timeseries-output "${output_dir}/gpu_utilization_timeseries.csv" \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output "${output_dir}/metadata.json" \
      --run-id "stage2-sweep-${label}" \
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
labels=()
for threshold in "${THRESHOLDS[@]}"; do
  for topk in "${TOP_KS[@]}"; do
    label="th${threshold}_k${topk}"
    output_dir="${SCRIPT_DIR}/results/stage2_sweep/${label}"
    if [[ "${SKIP_COMPLETED}" == "1" ]] && is_complete "${output_dir}"; then
      echo "Skipping completed ${label}"
    else
      tasks+=("${threshold} ${topk}")
      labels+=("${label}")
    fi
  done
done

echo "Prepared ${#tasks[@]} Stage 2 sweep combos with max parallelism ${MAX_PARALLEL}."
for ((start = 0; start < ${#tasks[@]}; start += MAX_PARALLEL)); do
  pids=()
  batch_labels=()
  for ((offset = 0; offset < MAX_PARALLEL && start + offset < ${#tasks[@]}; offset++)); do
    read -r threshold topk <<<"${tasks[start + offset]}"
    label="${labels[start + offset]}"
    echo "Starting ${label}"
    run_combo "${threshold}" "${topk}" &
    pids+=("$!")
    batch_labels+=("${label}")
  done

  failed=0
  for index in "${!pids[@]}"; do
    if wait "${pids[index]}"; then
      echo "Completed ${batch_labels[index]}"
    else
      echo "Failed ${batch_labels[index]}" >&2
      failed=1
    fi
  done
  if ((failed)); then
    echo "One or more Stage 2 sweep combos failed; inspect ${SCRIPT_DIR}/logs." >&2
  fi
done

echo "All Stage 2 sweep combos finished."
echo "Results: ${SCRIPT_DIR}/results/stage2_sweep"
