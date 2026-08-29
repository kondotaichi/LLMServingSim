#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXP_ROOT/../../../.." && pwd)"
SOURCE_EXP="experiments/2026-08-21-local-hongo-workload-gh200-test"
PRIOR_EXP="experiments/2026-08-24-simulate-both-local-and-cloudlike"
STORY_EXP="experiments/2026-08-25-storyline/02_merit_of_use_distributed_gpu/b_ran_aware_economics"
PEAK_LEVEL="${PEAK_LEVEL:-1}"
RAN_PROFILE="${RAN_PROFILE:-active10}"

case "$PEAK_LEVEL" in
  1|2|3|4|5|6|7|8|9|10) ;;
  *) echo "unsupported peak level: $PEAK_LEVEL" >&2; exit 2 ;;
esac
case "$RAN_PROFILE" in
  active10|active20) ;;
  *) echo "unsupported RAN profile: $RAN_PROFILE" >&2; exit 2 ;;
esac

cd "$REPO_ROOT"
python3 "$SCRIPT_DIR/prepare_configs.py"
mkdir -p "$EXP_ROOT/results" "$EXP_ROOT/logs"
export TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true

run_one() {
  local environment="$1" pp="$2"
  local result_environment="$environment"
  if [ "$environment" != "cloud" ] && [ "$RAN_PROFILE" = "active20" ]; then
    result_environment="${environment}_active20"
  fi
  local run_name="${result_environment}_pp${pp}_peak_${PEAK_LEVEL}x_nearest_kv"
  local output="$EXP_ROOT/results/$run_name"
  local log="$EXP_ROOT/logs/$run_name.log"
  local config workload gpu_placement backbone_gbps backbone_latency_ns

  if [ "$environment" = "cloud" ]; then
    config="$STORY_EXP/configs/cloud_pp${pp}.json"
    gpu_placement="$SOURCE_EXP/placements/hongo_gh200_gpus_clustered.csv"
    backbone_gbps=183.68
    backbone_latency_ns=16800
  elif [ "$environment" = "airan_apn" ]; then
    if [ "$RAN_PROFILE" = "active20" ]; then
      config="$STORY_EXP/configs/airan_active20_peak_pp${pp}.json"
    else
      config="$STORY_EXP/configs/airan_peak_pp${pp}.json"
    fi
    gpu_placement="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    backbone_gbps=10.7
    backbone_latency_ns=300500
  else
    if [ "$RAN_PROFILE" = "active20" ]; then
      config="$STORY_EXP/configs/airan_active20_peak_pp${pp}.json"
    else
      config="$STORY_EXP/configs/airan_peak_pp${pp}.json"
    fi
    gpu_placement="$SOURCE_EXP/placements/hongo_gh200_gpus.csv"
    backbone_gbps=1.0
    backbone_latency_ns=5000000
  fi

  if [ "$pp" -eq 1 ]; then
    if [ "$environment" = "cloud" ]; then
      workload="$SOURCE_EXP/workloads_clustered_cloud/hongo_peak_${PEAK_LEVEL}x_repeat600_seed1.jsonl"
    else
      workload="$SOURCE_EXP/workloads_cloud_aligned/hongo_peak_${PEAK_LEVEL}x_repeat600_seed1.jsonl"
    fi
  else
    workload="$PRIOR_EXP/workloads_no_ran_pp2/hongo_peak_${PEAK_LEVEL}x_repeat600_seed1.jsonl"
  fi

  mkdir -p "$output"
  if [ -f "$output/requests.csv" ]; then
    local rows
    rows="$(awk 'END {print NR - 1}' "$output/requests.csv")"
    if [ "$rows" -eq 600 ]; then
      echo "[$run_name skip complete]"
      return 0
    fi
  fi

  echo "[$run_name start]"
  python3 -m serving \
    --cluster-config "$config" \
    --dataset "$workload" \
    --request-routing-policy NEAREST_KV \
    --num-reqs 600 \
    --max-num-seqs 1024 \
    --max-num-batched-tokens 8192 \
    --block-size 16 \
    --dtype bfloat16 \
    --kv-cache-dtype auto \
    --enable-chunked-prefill \
    --enable-prefix-caching \
    --gpu-backbone-bandwidth-gbps "$backbone_gbps" \
    --apn-fixed-propagation-ns "$backbone_latency_ns" \
    --kv-staging-bandwidth-gbytes-per-s 297.8 \
    --kv-staging-latency-ns 2650 \
    --inputs-root "/tmp/astra_runs/storyline_02b_${run_name}" \
    --output "$output/requests.csv" \
    --geographic-user-output "$output/users.csv" \
    --geographic-gpu-output "$output/gpus.csv" \
    --geographic-metadata-output "$output/metadata.json" \
    --geographic-users-csv "$SOURCE_EXP/placements/hongo_users_8gpu.csv" \
    --geographic-gpus-csv "$gpu_placement" \
    --run-id "storyline-02b-${run_name}" \
    --log-level WARNING \
    > "$log" 2>&1
  echo "[$run_name complete]"
}

run_one cloud 1 &
pid_cloud_pp1=$!
run_one cloud 2 &
pid_cloud_pp2=$!
run_one airan_apn 1 &
pid_airan_apn_pp1=$!
run_one airan_apn 2 &
pid_airan_apn_pp2=$!
run_one airan_wan 1 &
pid_airan_wan_pp1=$!
run_one airan_wan 2 &
pid_airan_wan_pp2=$!

failed=0
wait "$pid_cloud_pp1" || failed=1
wait "$pid_cloud_pp2" || failed=1
wait "$pid_airan_apn_pp1" || failed=1
wait "$pid_airan_apn_pp2" || failed=1
wait "$pid_airan_wan_pp1" || failed=1
wait "$pid_airan_wan_pp2" || failed=1
exit "$failed"
