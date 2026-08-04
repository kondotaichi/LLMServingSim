#!/usr/bin/env bash
# Three-arm comparison at a pressure level selected by run_pressure_probe.sh.
set -euo pipefail

EXP="experiments/2026-08-02-kagosima-tokyo"
LOAD="${LOAD:-original}"
LOAD_LABEL="${LOAD//./p}"
CFG="${EXP}/configs"
WL="${EXP}/workloads"
RES="${EXP}/results/proposal_${LOAD_LABEL}"
LOG="${EXP}/logs"
PL="${EXP}/placements"

COMMON=(
  --num-reqs 300
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
  --pp-size 2
  --graph-converter in-process
  --trace-io buffered
  --log-level WARNING
)

run_sim() {
  local arm="$1" config="$2" dataset="$3" users_csv="$4" gpus_csv="$5"
  shift 5
  local extra=("$@")
  local run_name="proposal-${LOAD_LABEL}-${arm}"
  local out="${RES}/${arm}"
  mkdir -p "${out}" "${LOG}"

  TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  PYTHONUNBUFFERED=1 python3 -u -m serving \
    --cluster-config "${config}" \
    --dataset "${dataset}" \
    "${COMMON[@]}" \
    "${extra[@]}" \
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

echo "=== 3 proposal arms in parallel with ${LOAD_LABEL} arrivals ==="

run_sim "all_tokyo_baseline" \
  "${CFG}/all_tokyo_12gpu.json" \
  "${WL}/all_tokyo_pp2/reference_${LOAD_LABEL}_seed1.jsonl" \
  "${PL}/all_tokyo_users.csv" "${PL}/all_tokyo_gpus.csv" &

run_sim "kg_baseline" \
  "${CFG}/kagoshima_tokyo_12gpu.json" \
  "${WL}/kagoshima_tokyo_pp2/reference_${LOAD_LABEL}_seed1.jsonl" \
  "${PL}/kagoshima_tokyo_users.csv" "${PL}/kagoshima_tokyo_gpus.csv" &

run_sim "kg_proposed" \
  "${CFG}/kagoshima_tokyo_12gpu.json" \
  "${WL}/kagoshima_tokyo_pp2/reference_${LOAD_LABEL}_seed1.jsonl" \
  "${PL}/kagoshima_tokyo_users.csv" "${PL}/kagoshima_tokyo_gpus.csv" \
  --enable-proactive-kv-prewarm \
  --proactive-kv-prewarm-pressure-threshold 0.6 \
  --proactive-kv-prewarm-top-k 3 \
  --proactive-kv-prewarm-output "${RES}/kg_proposed/proactive_migration_log.csv" &

wait
echo "=== All 3 proposal arms complete ==="

for arm in all_tokyo_baseline kg_baseline kg_proposed; do
  echo "--- ${arm} ---"
  python3 "${EXP}/scripts/summarize_pressure_run.py" "${RES}/${arm}/requests.csv"
done
