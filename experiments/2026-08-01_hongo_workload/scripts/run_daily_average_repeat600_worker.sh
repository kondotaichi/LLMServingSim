#!/usr/bin/env bash
# Run one method for the repeated daily-average workload.

set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <arm: 1-4>" >&2
  exit 2
fi

ARM="$1"
EXP="experiments/2026-08-01_hongo_workload"
PREFIX="daily_average_repeat600_seed1"

case "$ARM" in
  1)
    NAME="no_redirect"
    POLICY="NEAREST_KV"
    CLUSTER="rtx4090_hongo.json"
    DATASET="hongo_daily_average_repeat600_seed1.jsonl"
    EXTRA=()
    ;;
  2)
    NAME="redirect_no_kv"
    POLICY="NEAREST_MIGRATE"
    CLUSTER="rtx4090_hongo.json"
    DATASET="hongo_daily_average_repeat600_seed1.jsonl"
    EXTRA=()
    ;;
  3)
    NAME="redirect_kv_nopp"
    POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
    CLUSTER="rtx4090_hongo.json"
    DATASET="hongo_daily_average_repeat600_seed1.jsonl"
    EXTRA=()
    ;;
  4)
    NAME="redirect_kv_pp2"
    POLICY="NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE"
    CLUSTER="rtx4090_hongo_pp2.json"
    DATASET="hongo_daily_average_repeat600_seed1_pp2.jsonl"
    EXTRA=(--pp-size 2)
    ;;
  *)
    echo "unsupported arm: $ARM" >&2
    exit 2
    ;;
esac

OUTPUT="$EXP/results/${PREFIX}_${ARM}_${NAME}"
mkdir -p "$OUTPUT" "$EXP/logs"

echo "[daily average start] ${ARM}_${NAME}"
python3 -m serving \
  --cluster-config "$EXP/configs/$CLUSTER" \
  --dataset "$EXP/workloads/$DATASET" \
  --request-routing-policy "$POLICY" \
  --num-reqs 600 \
  --max-num-seqs 128 \
  --max-num-batched-tokens 2048 \
  --gpu-backbone-bandwidth-gbps 10.7 \
  --apn-fixed-propagation-ns 300500 \
  --kv-staging-bandwidth-gbytes-per-s 33.8 \
  --kv-staging-latency-ns 102.9 \
  --inputs-root "/tmp/astra_runs/${PREFIX}_${ARM}_${NAME}" \
  --output "$OUTPUT/requests.csv" \
  --geographic-user-output "$OUTPUT/users.csv" \
  --geographic-gpu-output "$OUTPUT/gpus.csv" \
  --geographic-metadata-output "$OUTPUT/metadata.json" \
  --geographic-users-csv "$EXP/placements/hongo_users.csv" \
  --geographic-gpus-csv "$EXP/placements/hongo_gpus.csv" \
  --run-id "hongo-${PREFIX}-${ARM}-${NAME}" \
  --log-level WARNING \
  "${EXTRA[@]}" \
  > "$EXP/logs/${PREFIX}_${ARM}_${NAME}.log" 2>&1
rc=$?
echo "[daily average done rc=$rc] ${ARM}_${NAME}"
exit "$rc"
