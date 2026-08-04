#!/usr/bin/env bash
# Two no-redirect, no-cross-instance-KV-migration PP=1 baselines.
set -euo pipefail

EXP="experiments/2026-08-02-kagosima-tokyo"
LOAD="${LOAD:-rate4}"
LOAD_LABEL="${LOAD//./p}"
CLUSTER_CONFIG="experiments/2026-08-01_hongo_workload/configs/rtx4090_hongo.json"
RES="${EXP}/results/pp1_baseline_${LOAD_LABEL}"
LOG="${EXP}/logs"
PL="${EXP}/placements"

COMMON=(
  --num-reqs 300
  --request-routing-policy NEAREST_KV
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
  --pp-size 1
  --graph-converter in-process
  --trace-io buffered
  --log-level WARNING
)

run_sim() {
  local arm="$1" dataset="$2" users_csv="$3" gpus_csv="$4"
  local run_name="pp1-baseline-${LOAD_LABEL}-${arm}"
  local out="${RES}/${arm}"
  mkdir -p "${out}" "${LOG}"

  TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  PYTHONUNBUFFERED=1 python3 -u -m serving \
    --cluster-config "${CLUSTER_CONFIG}" \
    --dataset "${dataset}" \
    "${COMMON[@]}" \
    --inputs-root "/tmp/astra_runs/${run_name}" \
    --output "${out}/requests.csv" \
    --geographic-user-output "${out}/users.csv" \
    --geographic-gpu-output "${out}/gpus.csv" \
    --geographic-metadata-output "${out}/metadata.json" \
    --geographic-users-csv "${users_csv}" \
    --geographic-gpus-csv "${gpus_csv}" \
    --gpu-utilization-timeseries-output "${out}/gpu_utilization_timeseries.csv" \
    --gpu-utilization-window-ns 1000000000 \
    --run-id "${run_name}" \
    >"${LOG}/${run_name}.log" 2>&1
  echo "[done] ${arm}"
}

echo "=== 2 PP=1 no-redirect baselines sequentially ==="

run_sim "all_tokyo" \
  "${EXP}/workloads/all_tokyo_pp1/reference_${LOAD_LABEL}_seed1.jsonl" \
  "${PL}/all_tokyo_users.csv" "${PL}/all_tokyo_gpus.csv"

run_sim "kagoshima_tokyo" \
  "${EXP}/workloads/kagoshima_tokyo_pp1/reference_${LOAD_LABEL}_seed1.jsonl" \
  "${PL}/kagoshima_tokyo_users.csv" "${PL}/kagoshima_tokyo_gpus.csv"

echo "=== Both PP=1 baselines complete ==="

for arm in all_tokyo kagoshima_tokyo; do
  echo "--- ${arm} ---"
  python3 "${EXP}/scripts/summarize_pressure_run.py" "${RES}/${arm}/requests.csv"
done
