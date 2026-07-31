#!/usr/bin/env bash
# Resume everything that was interrupted mid-run: Stage 4's remaining
# input8000_reuse00, and the 4 "C only" (Method C without Method A) probes
# needed for the four-arm breakdown/CDF figures
# (scripts/plot_four_arm_comparison.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

SIM_CONTAINER="${SIM_CONTAINER:-llmservingsim_sim_local}"
if [[ "$(docker inspect -f '{{.State.Running}}' "${SIM_CONTAINER}" 2>/dev/null)" != "true" ]]; then
  echo "Starting simulator container ${SIM_CONTAINER}..."
  docker start "${SIM_CONTAINER}" >/dev/null
fi

echo "=== Resuming Stage 4 (input8000_reuse00 only, others already complete) ==="
MAX_PARALLEL=1 ./experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_c_proactive_kv_prewarm/run_stage4_sweep.sh

echo "=== Resuming C-only probes (PP1/PP2 x reuse025/reuse05) ==="
DATASET_DIR="experiments/2026-07-22_pp2_five_workloads/workloads"
OUT_ROOT="experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_c_proactive_kv_prewarm/results/c_only"
LOG_DIR="experiments/2026-07-28_pp_schedule_conceal_and_speculative/method_c_proactive_kv_prewarm/logs"

run_pp2() {
  local workload="$1"
  local out_dir="${OUT_ROOT}/pp2_${workload}"
  mkdir -p "${out_dir}"
  docker exec "${SIM_CONTAINER}" bash -lc "
    cd /app/LLMServingSim && \
    TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true PYTHONUNBUFFERED=1 python3 -u -m serving \
      --cluster-config configs/cluster/five_node_rtx4090_apn.json --pp-size 2 \
      --dataset ${DATASET_DIR}/${workload}.jsonl --num-reqs 300 \
      --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
      --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
      --enable-chunked-prefill --enable-prefix-caching \
      --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
      --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
      --enable-proactive-kv-prewarm \
      --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3 \
      --proactive-kv-prewarm-output ${out_dir}/proactive_migration_log.csv \
      --graph-converter in-process --trace-io buffered \
      --output ${out_dir}/requests.csv \
      --geographic-user-output ${out_dir}/users.csv \
      --geographic-gpu-output ${out_dir}/gpus.csv \
      --gpu-utilization-timeseries-output ${out_dir}/gpu_utilization_timeseries.csv \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output ${out_dir}/metadata.json \
      --run-id c-only-pp2-${workload} --log-level WARNING
  " > "${LOG_DIR}/c_only_pp2_${workload}.log" 2>&1
}

run_pp1() {
  local workload="$1"
  local out_dir="${OUT_ROOT}/pp1_${workload}"
  mkdir -p "${out_dir}"
  docker exec "${SIM_CONTAINER}" bash -lc "
    cd /app/LLMServingSim && \
    TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true PYTHONUNBUFFERED=1 python3 -u -m serving \
      --cluster-config configs/cluster/ten_node_rtx4090_apn.json --pp-size 1 \
      --dataset ${DATASET_DIR}/${workload}_pp1.jsonl --num-reqs 300 \
      --request-routing-policy NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE \
      --dtype bfloat16 --kv-cache-dtype auto --max-num-seqs 128 --max-num-batched-tokens 2048 \
      --enable-chunked-prefill --enable-prefix-caching \
      --gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500 \
      --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9 \
      --enable-proactive-kv-prewarm \
      --proactive-kv-prewarm-pressure-threshold 0.8 --proactive-kv-prewarm-top-k 3 \
      --proactive-kv-prewarm-output ${out_dir}/proactive_migration_log.csv \
      --graph-converter in-process --trace-io buffered \
      --output ${out_dir}/requests.csv \
      --geographic-user-output ${out_dir}/users.csv \
      --geographic-gpu-output ${out_dir}/gpus.csv \
      --gpu-utilization-timeseries-output ${out_dir}/gpu_utilization_timeseries.csv \
      --gpu-utilization-window-ns 1000000000 \
      --geographic-metadata-output ${out_dir}/metadata.json \
      --run-id c-only-pp1-${workload} --log-level WARNING
  " > "${LOG_DIR}/c_only_pp1_${workload}.log" 2>&1
}

is_done() { [[ -s "$1/requests.csv" ]] && [[ "$(wc -l <"$1/requests.csv")" -eq 301 ]]; }

is_done "${OUT_ROOT}/pp2_input8000_reuse025" || run_pp2 input8000_reuse025 &
is_done "${OUT_ROOT}/pp2_input8000_reuse05"  || run_pp2 input8000_reuse05 &
is_done "${OUT_ROOT}/pp1_input8000_reuse025" || run_pp1 input8000_reuse025 &
is_done "${OUT_ROOT}/pp1_input8000_reuse05"  || run_pp1 input8000_reuse05 &
wait
echo "=== C-only probes done ==="

echo "=== Re-plotting four-arm comparison (now with C only included) ==="
(cd "${SCRIPT_DIR}" && python3 scripts/plot_four_arm_comparison.py)

echo "All done."
