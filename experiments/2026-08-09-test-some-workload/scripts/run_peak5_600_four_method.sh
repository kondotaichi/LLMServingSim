#!/usr/bin/env bash
# Run four non-proactive methods on the session-stratified 600-request workload.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${EXP_ROOT}/../.." && pwd)"
EXP="experiments/2026-08-09-test-some-workload"
RUN_TAG="peak_5x_600_four_method"
RESULT_ROOT="${EXP}/results/${RUN_TAG}"
LOG_ROOT="${EXP}/logs/${RUN_TAG}"
LOG_SUFFIX="${LOG_SUFFIX:-}"

cd "${REPO_ROOT}"
mkdir -p "${RESULT_ROOT}" "${LOG_ROOT}"

run_one() {
  local arm_num="$1" arm_name="$2" policy="$3" pp_size="$4"
  local dataset cluster max_num_seqs max_batched_tokens pp_flags=()
  if [[ "${pp_size}" == "2" ]]; then
    dataset="${EXP}/workloads/full/hongo_peak_5x_600_seed1_pp2.jsonl"
    cluster="${EXP}/configs/rtx4090_hongo_pp2.json"
    max_num_seqs=64
    max_batched_tokens=4096
    pp_flags=(--pp-size 2)
  else
    dataset="${EXP}/workloads/full/hongo_peak_5x_600_seed1.jsonl"
    cluster="${EXP}/configs/rtx4090_hongo.json"
    max_num_seqs=32
    max_batched_tokens=2048
  fi

  local arm="${arm_num}_${arm_name}"
  local outdir="${RESULT_ROOT}/${arm}"
  local logfile="${LOG_ROOT}/${arm}${LOG_SUFFIX}.log"
  mkdir -p "${outdir}"
  echo "[start] ${arm} PP=${pp_size} policy=${policy} log=${logfile}"

  PYTHONUNBUFFERED=1 TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  python3 -u -m serving \
    --cluster-config "${cluster}" \
    --dataset "${dataset}" \
    --request-routing-policy "${policy}" \
    --num-reqs 600 \
    --max-num-seqs "${max_num_seqs}" \
    --max-num-batched-tokens "${max_batched_tokens}" \
    --enable-chunked-prefill \
    --enable-prefix-caching \
    --gpu-backbone-bandwidth-gbps 10.7 \
    --apn-fixed-propagation-ns 300500 \
    --kv-staging-bandwidth-gbytes-per-s 33.8 \
    --kv-staging-latency-ns 102.9 \
    --enable-scheduler-hide-kv-migration \
    --graph-converter in-process \
    --trace-io buffered \
    --inputs-root "/tmp/astra_runs/method_fix_${RUN_TAG}_${arm_num}" \
    --output "${outdir}/requests.csv" \
    --geographic-user-output "${outdir}/users.csv" \
    --geographic-gpu-output "${outdir}/gpus.csv" \
    --geographic-metadata-output "${outdir}/metadata.json" \
    --geographic-users-csv "${EXP}/placements/hongo_users.csv" \
    --geographic-gpus-csv "${EXP}/placements/hongo_gpus.csv" \
    --gpu-utilization-timeseries-output "${outdir}/gpu_utilization_timeseries.csv" \
    --gpu-utilization-window-ns 1000000000 \
    --run-id "method-fix-${RUN_TAG}-${arm_num}" \
    --log-level WARNING \
    "${pp_flags[@]}" \
    2>&1 | tee "${logfile}"
  local rc=${PIPESTATUS[0]}
  echo "[done rc=${rc}] ${arm}"
  return "${rc}"
}

python3 "${EXP}/scripts/build_peak5_600.py"

failures=0
declare -a pids=()
all_specs=(
  "1|naive|NEAREST_KV|1"
  "2|redirect_cold|NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE|1"
  "3|redirect_kv_pp1|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|1"
  "4|redirect_kv_pp2|NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE|2"
)

selected_specs=()
if (( $# == 0 )); then
  selected_specs=("${all_specs[@]}")
else
  for selector in "$@"; do
    matched=false
    for spec in "${all_specs[@]}"; do
      IFS='|' read -r arm_num arm_name _ _ <<< "${spec}"
      if [[ "${selector}" == "${arm_num}" || "${selector}" == "${arm_name}" ]]; then
        selected_specs+=("${spec}")
        matched=true
        break
      fi
    done
    if [[ "${matched}" == false ]]; then
      echo "Unknown arm selector: ${selector}" >&2
      exit 2
    fi
  done
fi

for spec in "${selected_specs[@]}"; do
  IFS='|' read -r arm_num arm_name policy pp_size <<< "${spec}"
  run_one "${arm_num}" "${arm_name}" "${policy}" "${pp_size}" &
  pids+=("$!")
  sleep 10
done

for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    failures=$((failures + 1))
  fi
done

echo "=== Peak 5x 600-request four-method run finished: failures=${failures} ==="
exit "${failures}"
