#!/usr/bin/env bash
# Run the PP=1 geographic placement arms for the Hongo 1x-10x load sweep.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${EXP_ROOT}/../.." && pwd)"
EXP_REL="experiments/2026-08-02-kagosima-tokyo"

SEED="${SEED:-1}"
NUM_REQS="${NUM_REQS:-300}"
LEVELS="${LEVELS:-1 2 3 4 5 6 7 8 9 10}"
PREPARE_WORKLOADS="${PREPARE_WORKLOADS:-auto}"
MAX_PARALLEL="${MAX_PARALLEL:-5}"

CLUSTER_CONFIG="experiments/2026-08-01_hongo_workload/configs/rtx4090_hongo.json"
WL="${EXP_REL}/workloads"
RES="${EXP_REL}/results/hongo_1x10x_pp1"
LOG="${EXP_REL}/logs/hongo_1x10x_pp1"
PL="${EXP_REL}/placements"

COMMON=(
  --num-reqs "${NUM_REQS}"
  --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE
  --dtype bfloat16
  --kv-cache-dtype auto
  --max-num-seqs 128
  --max-num-batched-tokens 2048
  --enable-chunked-prefill
  --enable-prefix-caching
  --gpu-backbone-bandwidth-gbps 10.7
  --apn-fixed-propagation-ns 300500
  --kv-staging-bandwidth-gbytes-per-s 33.8
  --kv-staging-latency-ns 102.9
  --enable-scheduler-hide-kv-migration
  --pp-size 1
  --graph-converter in-process
  --trace-io buffered
  --log-level WARNING
)

load_name() {
  if [[ "$1" == "1" ]]; then
    echo "busy_hour"
  else
    echo "peak_$1x"
  fi
}

prepare_workloads() {
  local level name path missing=0
  if [[ "${PREPARE_WORKLOADS}" == "0" || "${PREPARE_WORKLOADS}" == "false" ]]; then
    return
  fi
  for level in ${LEVELS}; do
    name="$(load_name "${level}")"
    for layout in all_tokyo kagoshima_tokyo; do
      path="${WL}/${layout}_pp1/hongo_${name}_seed${SEED}.jsonl"
      if [[ ! -f "${path}" ]]; then
        missing=1
      fi
    done
  done
  if [[ "${PREPARE_WORKLOADS}" != "auto" || "${missing}" == "1" ]]; then
    python3 "${EXP_REL}/scripts/prepare_hongo_sweep_workloads.py" \
      --num-reqs "${NUM_REQS}" \
      --seed "${SEED}" \
      --pp-size 1
  fi
}

run_sim() {
  local level="$1" arm="$2" config="$3" dataset="$4" users="$5" gpus="$6"
  shift 6
  local out="${RES}/${level}x/${arm}"
  local run_name="kagoshima-tokyo-hongo-pp1-${level}x-${arm}-seed${SEED}"
  mkdir -p "${out}" "${LOG}"
  if [[ -s "${out}/requests.csv" ]]; then
    echo "[skip] ${level}x ${arm}"
    return
  fi

  if ! TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  PYTHONUNBUFFERED=1 python3 -u -m serving \
    --cluster-config "${config}" \
    --dataset "${dataset}" \
    "${COMMON[@]}" \
    "$@" \
    --inputs-root "/tmp/astra_runs/${run_name}" \
    --output "${out}/requests.csv" \
    --geographic-user-output "${out}/users.csv" \
    --geographic-gpu-output "${out}/gpus.csv" \
    --geographic-metadata-output "${out}/metadata.json" \
    --geographic-users-csv "${users}" \
    --geographic-gpus-csv "${gpus}" \
    --gpu-utilization-timeseries-output "${out}/gpu_utilization_timeseries.csv" \
    --gpu-utilization-window-ns 1000000000 \
    --run-id "${run_name}" \
    >"${LOG}/${run_name}.log" 2>&1; then
    echo "[failed] ${level}x ${arm}" >&2
    return 1
  fi
  echo "[done] ${level}x ${arm}"
}

run_arm() {
  local level="$1" arm="$2" name
  name="$(load_name "${level}")"
  case "${arm}" in
    all_tokyo_baseline)
      run_sim "${level}" "${arm}" \
        "${CLUSTER_CONFIG}" \
        "${WL}/all_tokyo_pp1/hongo_${name}_seed${SEED}.jsonl" \
        "${PL}/all_tokyo_users.csv" "${PL}/all_tokyo_gpus.csv"
      ;;
    kg_baseline)
      run_sim "${level}" "${arm}" \
        "${CLUSTER_CONFIG}" \
        "${WL}/kagoshima_tokyo_pp1/hongo_${name}_seed${SEED}.jsonl" \
        "${PL}/kagoshima_tokyo_users.csv" "${PL}/kagoshima_tokyo_gpus.csv"
      ;;
    kg_proposed)
      run_sim "${level}" "${arm}" \
        "${CLUSTER_CONFIG}" \
        "${WL}/kagoshima_tokyo_pp1/hongo_${name}_seed${SEED}.jsonl" \
        "${PL}/kagoshima_tokyo_users.csv" "${PL}/kagoshima_tokyo_gpus.csv" \
        --enable-proactive-kv-prewarm \
        --proactive-kv-prewarm-pressure-threshold 0.6 \
        --proactive-kv-prewarm-top-k 3 \
        --proactive-kv-prewarm-output "${RES}/${level}x/kg_proposed/proactive_migration_log.csv"
      ;;
  esac
}

run_arm_with_retry() {
  local level="$1" arm="$2"
  if run_arm "${level}" "${arm}"; then
    return
  fi
  echo "[retry] ${level}x ${arm} after transient failure"
  sleep 5
  run_arm "${level}" "${arm}"
}

main() {
  cd "${REPO_ROOT}"
  prepare_workloads
  local level arm
  local pids=()
  echo "=== running PP=1 Hongo sweep with max ${MAX_PARALLEL} parallel jobs ==="
  for level in ${LEVELS}; do
    for arm in all_tokyo_baseline kg_baseline kg_proposed; do
      run_arm_with_retry "${level}" "${arm}" &
      pids+=("$!")
      sleep 2
      if (( ${#pids[@]} >= MAX_PARALLEL )); then
        wait "${pids[0]}"
        pids=("${pids[@]:1}")
      fi
    done
  done
  for pid in "${pids[@]}"; do
    wait "${pid}"
  done
  python3 "${EXP_REL}/scripts/summarize_hongo_1x10x.py" --results "${RES}"
  echo "=== completed PP=1 Hongo levels: ${LEVELS} ==="
}

main "$@"
