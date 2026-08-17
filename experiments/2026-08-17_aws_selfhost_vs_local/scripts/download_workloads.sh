#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 s3://bucket/prefix" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
experiment_dir="$repo_root/experiments/2026-08-17_aws_selfhost_vs_local"
manifest="$experiment_dir/workloads/manifests/hongo_seed1.csv"
destination="$experiment_dir/workloads/hongo"
s3_prefix="${1%/}"

command -v aws >/dev/null || {
  echo "aws CLI is required" >&2
  exit 1
}

mkdir -p "$destination"
while IFS=, read -r load filename source_path object_key requests unique_sessions return_visit_rate target_rate_rps timeline_s size_bytes sha256; do
  if [[ "$load" == "load" ]]; then
    continue
  fi
  aws s3 cp "$s3_prefix/$object_key" "$destination/$filename" --only-show-errors
done < "$manifest"

python3 "$experiment_dir/scripts/validate_workloads.py" \
  --manifest "$manifest" \
  --directory "$destination"
echo "Downloaded and verified Hongo workloads in $destination"
