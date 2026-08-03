#!/usr/bin/env bash
# 3-arm heavy-only comparison:
#   all_tokyo_pp2_spec vs kg_redirect_kv_pp2 vs kg_redirect_kv_pp2_spec
# Execute inside the simulator container from /app/LLMServingSim.
set -euo pipefail

EXP="experiments/2026-08-02-kagosima-tokyo"
CFG="${EXP}/configs"
WL="${EXP}/workloads"
RES="${EXP}/results"
LOG="${EXP}/logs"
PL="${EXP}/placements"
mkdir -p "${LOG}"

COMMON=(
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
  --graph-converter in-process
  --trace-io buffered
  --log-level WARNING
)

# run_sim <arm> <config> <workload_jsonl> <policy> <users_csv> <gpus_csv> [extra...]
run_sim() {
  local arm="$1" config="$2" wl="$3" policy="$4" users_csv="$5" gpus_csv="$6"
  shift 6
  local extra=("$@")
  local out="${RES}/${arm}"
  mkdir -p "${out}"
  local logfile="${LOG}/$(echo "${arm}" | tr '/' '_').log"

  TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true \
  PYTHONUNBUFFERED=1 python3 -u -m serving \
    --cluster-config "${config}" \
    --dataset "${wl}" \
    --num-reqs 1000 \
    --request-routing-policy "${policy}" \
    "${COMMON[@]}" \
    "${extra[@]}" \
    --output                                "${out}/requests.csv" \
    --geographic-user-output                "${out}/users.csv" \
    --geographic-gpu-output                 "${out}/gpus.csv" \
    --geographic-metadata-output            "${out}/metadata.json" \
    --geographic-users-csv                  "${users_csv}" \
    --geographic-gpus-csv                   "${gpus_csv}" \
    --gpu-utilization-timeseries-output     "${out}/gpu_utilization_timeseries.csv" \
    --gpu-utilization-window-ns 1000000000 \
    --run-id "$(echo "${arm}" | tr '/' '-')" \
    >"${logfile}" 2>&1
  echo "[done] ${arm}"
}

AT_USERS="${PL}/all_tokyo_users.csv"
AT_GPUS="${PL}/all_tokyo_gpus.csv"
KG_USERS="${PL}/kagoshima_tokyo_users.csv"
KG_GPUS="${PL}/kagoshima_tokyo_gpus.csv"
AT_CFG="${CFG}/all_tokyo_12gpu.json"
KG_CFG="${CFG}/kagoshima_tokyo_12gpu.json"

# ── All 3 arms in parallel ─────────────────────────────────────────────────
echo "=== 3 arms in parallel ==="

run_sim "all_tokyo/heavy" \
  "${AT_CFG}" "${WL}/all_tokyo_pp2/heavy_seed1.jsonl" \
  NEAREST_MIGRATE_KV "${AT_USERS}" "${AT_GPUS}" \
  --pp-size 2 \
  --enable-proactive-kv-prewarm &

run_sim "kg_pp2/heavy" \
  "${KG_CFG}" "${WL}/kagoshima_tokyo_pp2/heavy_seed1.jsonl" \
  NEAREST_MIGRATE_KV "${KG_USERS}" "${KG_GPUS}" \
  --pp-size 2 &

run_sim "kg_pp2_spec/heavy" \
  "${KG_CFG}" "${WL}/kagoshima_tokyo_pp2/heavy_seed1.jsonl" \
  NEAREST_MIGRATE_KV "${KG_USERS}" "${KG_GPUS}" \
  --pp-size 2 \
  --enable-proactive-kv-prewarm &

wait

echo "=== All 3 arms complete. Run scripts/analyze_cost.py to summarize. ==="
